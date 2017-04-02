from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
from urllib import urlencode
from urllib2 import Request, urlopen, URLError
from urlparse import urljoin
import random
import time
import timeit
import datetime

from ..MPLogger import loggingclient
from ..utilities import domain_utils
from utils.webdriver_extensions import wait_until_loaded
from browser_commands import get_website, bot_mitigation

# Link text ranking
_TYPE_TEXT = 'text'
_TYPE_HREF = 'href'
_FLAG_NONE = 0
_FLAG_STAY_ON_PAGE = 1
_LINK_TEXT_RANK = [
    # probably newsletters
    (_TYPE_TEXT, 'newsletter', 10, _FLAG_NONE),
    (_TYPE_TEXT, 'weekly ad',   9, _FLAG_NONE),
    (_TYPE_TEXT, 'subscribe',   9, _FLAG_NONE),
    (_TYPE_TEXT, 'inbox',       8, _FLAG_NONE),
    (_TYPE_TEXT, 'email',       7, _FLAG_NONE),
    (_TYPE_TEXT, 'sale alert',  6, _FLAG_NONE),

    # sign-up links (for something?)
    (_TYPE_TEXT, 'signup',   5, _FLAG_NONE),
    (_TYPE_TEXT, 'sign up',  5, _FLAG_NONE),
    (_TYPE_TEXT, 'register', 4, _FLAG_NONE),
    (_TYPE_TEXT, 'create',   4, _FLAG_NONE),

    # news articles (sometimes sign-up links are on these pages...)
    (_TYPE_HREF, '/article', 3, _FLAG_NONE),
    (_TYPE_HREF, 'news/', 3, _FLAG_NONE),
    (_TYPE_HREF, '/' + str(datetime.datetime.now().year), 2, _FLAG_NONE),
    (_TYPE_HREF, 'technology', 1, _FLAG_NONE),
    (_TYPE_HREF, 'business', 1, _FLAG_NONE),
    (_TYPE_HREF, 'politics', 1, _FLAG_NONE),
    (_TYPE_HREF, 'entertainment', 1, _FLAG_NONE),

    # country selectors (for country-selection landing pages)
    (_TYPE_HREF, '/us/', 1, _FLAG_STAY_ON_PAGE),
    (_TYPE_HREF, '=us&', 1, _FLAG_STAY_ON_PAGE),
    (_TYPE_HREF, 'en-us', 1, _FLAG_STAY_ON_PAGE),
]
_LINK_RANK_SKIP = 6  # minimum rank to select immediately (skipping the rest of the links)
_LINK_MATCH_TIMEOUT = 20  # maximum time to match links, in seconds
_LINK_TEXT_BLACKLIST = ['unsubscribe', 'mobile', 'phone']

# Other constants
_PAGE_LOAD_TIME = 5  # time to wait for pages to load (in seconds)

def find_newsletters(url, api, num_links, visit_id, webdriver, proxy_queue, browser_params,
                     manager_params, extension_socket, page_timeout=8):
    """Finds a newsletter form on the page. If not found, visits <num_links>
    internal links and scans those pages for a form. Submits the form if found.
    """
    # get the site
    webdriver.set_page_load_timeout(page_timeout)
    get_website(url, 0, visit_id, webdriver, proxy_queue, browser_params, extension_socket)

    # connect to logger
    logger = loggingclient(*manager_params['logger_address'])

    # try to find newsletter form on landing page
    if _find_and_fill_form(webdriver, api, logger):
        return

    # otherwise, scan more pages
    main_handle = webdriver.current_window_handle
    visited_links = set()
    for i in xrange(num_links):
        # get all links on the page
        links = webdriver.find_elements_by_tag_name('a')

        current_url = webdriver.current_url
        current_ps1 = domain_utils.get_ps_plus_1(current_url)

        # find links to click
        match_links = []
        start_time = timeit.default_timer()
        for link in links:
            try:
                if not link.is_displayed():
                    continue

                # check if link is valid and not already visited
                href = link.get_attribute('href')
                if href is None or href in visited_links:
                    continue

                # check if this is an internal link
                if not _is_internal_link(href, current_url, current_ps1):
                    continue

                link_text = link.text.lower()

                # skip links with blacklisted text
                blacklisted = False
                for bl_text in _LINK_TEXT_BLACKLIST:
                    if bl_text in link_text:
                        blacklisted = True
                        break
                if blacklisted:
                    continue

                # should we click this link?
                link_rank = 0
                for type, s, rank, flags in _LINK_TEXT_RANK:
                    if (type == _TYPE_TEXT and s in link_text) or (type == _TYPE_HREF and s in href):
                        link_rank = rank
                        match_links.append((link, rank, link_text, href, flags))
                        break
                if link_rank >= _LINK_RANK_SKIP:  # good enough, stop looking
                    break
            except:
                logger.error("error while looping through links...")

            # quit if too much time passed (for some reason, this is really slow...)
            if match_links and timeit.default_timer() - start_time > _LINK_MATCH_TIMEOUT:
                break

        # find the best link to click
        if not match_links:
            break  # no more links to click
        match_links.sort(key=lambda l: l[1])
        next_link = match_links[-1]
        visited_links.add(next_link[3])

        # click the link
        try:
            # load the page
            logger.info("clicking on link '%s' - %s" % (next_link[2], next_link[3]))
            next_link[0].click()
            wait_until_loaded(webdriver, _PAGE_LOAD_TIME)
            if browser_params['bot_mitigation']:
                bot_mitigation(webdriver)

            # find newsletter form
            if _find_and_fill_form(webdriver, api, logger):
                return

            # should we stay on this page?
            if next_link[4] & _FLAG_STAY_ON_PAGE:
                continue

            # go back
            webdriver.back()
            wait_until_loaded(webdriver, _PAGE_LOAD_TIME)

            # check other windows (ex. pop-ups)
            windows = webdriver.window_handles
            if len(windows) > 1:
                form_found_in_popup = False
                for window in windows:
                    if window != main_handle:
                        webdriver.switch_to_window(window)
                        wait_until_loaded(webdriver, _PAGE_LOAD_TIME)

                        # find newsletter form
                        if _find_and_fill_form(webdriver, api, logger):
                            form_found_in_popup = True

                        webdriver.close()
                webdriver.switch_to_window(main_handle)
                time.sleep(1)

                if form_found_in_popup:
                    return
        except Exception:
            pass

def _get_email_from_api(api, webdriver, logger):
    """Registers an email address with the mail API, and returns the email."""
    data = urlencode({
        'site': webdriver.title.encode('ascii', 'replace'),
        'url': webdriver.current_url,
    })
    req = Request(api, data)
    response = urlopen(req)
    return response.read()

def _is_internal_link(href, url, ps1=None):
    """Returns whether the given link is an internal link."""
    if ps1 is None:
        ps1 = domain_utils.get_ps_plus_1(url)
    return domain_utils.get_ps_plus_1(urljoin(url, href)) == ps1

def _find_and_fill_form(webdriver, api, logger):
    """Finds and fills a form, and returns True if accomplished."""
    # try to find newsletter form on landing page
    newsletter_form = _find_newsletter_form(webdriver)
    if newsletter_form is None:
        return False

    current_url = webdriver.current_url
    email = _get_email_from_api(api, webdriver, logger)
    _form_fill_and_submit(newsletter_form, email, webdriver, False)
    logger.info('submitted form on [%s] with email [%s]', current_url, email)

    # fill any follow-up forms
    wait_until_loaded(webdriver, _PAGE_LOAD_TIME)  # wait if we got redirected
    follow_up_form = _find_newsletter_form(webdriver)
    if follow_up_form is not None:
        _form_fill_and_submit(follow_up_form, email, webdriver, True)

    return True

def _find_newsletter_form(webdriver):
    """Tries to find a form element on the page for newsletter sign-up.
    Returns None if no form was found.
    """
    # find all forms that match
    newsletter_forms = []
    forms = webdriver.find_elements_by_tag_name('form')
    for form in forms:
        if not form.is_displayed():
            continue

        # find words 'email' or 'newsletter' in the form
        form_html = form.get_attribute('outerHTML').lower()
        match = False
        if 'email' in form_html or 'newsletter' in form_html:
            # check if an input field contains an email element
            input_fields = form.find_elements_by_tag_name('input')
            for input_field in input_fields:
                type = input_field.get_attribute('type').lower()
                if type == 'email':
                    match = True
                    break
                elif type == 'text':
                    if _element_contains_text(input_field, ['email', 'e-mail', 'subscribe', 'newsletter']):
                        match = True
                        break

        # if this form matched, get some other ranking criteria:
        # rank modal/pop-up/dialogs higher, since these are likely to be sign-up forms
        if match:
            z_index = _get_z_index(form, webdriver)
            has_modal_text = 'modal' in form_html or 'dialog' in form_html
            newsletter_forms.append((form, (z_index, int(has_modal_text))))

    # no matches?
    if not newsletter_forms:
        return None

    # return highest ranked form
    newsletter_forms.sort(key=lambda x: x[1], reverse=True)
    return newsletter_forms[0][0]

def _get_z_index(element, webdriver):
    """Tries to find the actual z-index of an element, otherwise returns 0."""
    e = element
    while e is not None:
        try:
            # selenium is usually wrong, don't bother with this
            #z = element.value_of_css_property('z-index')
            #if z and z != 'auto':
            #    try:
            #        return int(z)
            #    except ValueError:
            #        pass

            # get z-index with javascript
            id = e.get_attribute('id')
            if id:
                scriptId = 'window.document.getElementById("%s")' % id
                script = 'return window.document.defaultView.getComputedStyle(%s, null).getPropertyValue("z-index")' % scriptId
                z = webdriver.execute_script(script)
                if z and z != 'auto':
                    try:
                        return int(z)
                    except ValueError:
                        pass

            # try the parent...
            e = e.find_element_by_xpath('..')  # throws exception when parent is the <html> tag
        except:
            break
    return 0

def _form_fill_and_submit(form, email, webdriver, clear):
    """Fills out a form and submits it, then waits for the response."""
    # try to fill all input fields in the form...
    input_fields = form.find_elements_by_tag_name('input')
    submit_button = None
    text_field = None
    fake_user = 'bobsmith' + str(random.randrange(0,1000))
    fake_tel = '212' + '555' + '01' + str(random.randrange(0,10)) + str(random.randrange(0,10))
    for input_field in input_fields:
        if not input_field.is_displayed():
            continue

        type = input_field.get_attribute('type').lower()
        if type == 'email':
            # using html5 "email" type, this is probably an email field
            _type_in_field(input_field, email, clear)
            text_field = input_field
        elif type == 'text':
            # try to decipher this based on field attributes
            if _element_contains_text(input_field, ['email', 'e-mail', 'subscribe', 'newsletter']):
                _type_in_field(input_field, email, clear)
            elif _element_contains_text(input_field, 'name'):
                if _element_contains_text(input_field, ['user', 'account']):
                    _type_in_field(input_field, fake_user, clear)
                elif _element_contains_text(input_field, 'first'):
                    _type_in_field(input_field, 'Bob', clear)
                elif _element_contains_text(input_field, 'last'):
                    _type_in_field(input_field, 'Smith', clear)
                elif _element_contains_text(input_field, 'company'):
                    _type_in_field(input_field, 'Smith & Co.', clear)
                elif _element_contains_text(input_field, 'title'):
                    _type_in_field(input_field, 'Mr.', clear)
                else:
                    _type_in_field(input_field, 'Bob Smith', clear)
            elif _element_contains_text(input_field, 'title'):
                _type_in_field(input_field, 'Mr.', clear)
            elif _element_contains_text(input_field, ['phone', 'tel', 'mobile']):
                _type_in_field(input_field, fake_tel, clear)
            elif _element_contains_text(input_field, ['zip', 'postal']):
                _type_in_field(input_field, '12345', clear)
            elif _element_contains_text(input_field, ['street', 'address']):
                if _element_contains_text(input_field, ['2', 'number']):
                    _type_in_field(input_field, 'Apt. 101', clear)
                elif _element_contains_text(input_field, '3'):
                    pass
                else:
                    _type_in_field(input_field, '101 Main St.', clear)
            elif _element_contains_text(input_field, 'city'):
                _type_in_field(input_field, 'Schenectady', clear)
            elif _element_contains_text(input_field, 'search'):
                pass
            else:
                # default: assume email
                _type_in_field(input_field, email, clear)
            text_field = input_field
        elif type == 'number':
            if _element_contains_text(input_field, ['phone', 'tel', 'mobile']):
                _type_in_field(input_field, fake_tel, clear)
            elif _element_contains_text(input_field, ['zip', 'postal']):
                _type_in_field(input_field, '12345', clear)
            else:
                _type_in_field(input_field, '12345', clear)
        elif type == 'checkbox' or type == 'radio':
            # check anything/everything
            if not input_field.is_selected():
                input_field.click()
        elif type == 'password':
            _type_in_field(input_field, 'p4S$w0rd123', clear)
        elif type == 'tel':
            _type_in_field(input_field, fake_tel, clear)
        elif type == 'submit' or type == 'button' or type == 'image':
            if _element_contains_text(input_field, ['submit', 'sign up', 'sign-up', 'signup']):
                submit_button = input_field
        elif type == 'reset' or type == 'hidden' or type == 'search':
            # common irrelevant input types
            pass
        else:
            # default: assume email
            _type_in_field(input_field, email, clear)

    # fill in 'select' fields
    select_fields = form.find_elements_by_tag_name('select')
    for select_field in select_fields:
        if not select_field.is_displayed():
            continue

        # select an appropriate element if possible,
        # otherwise second element (to skip blank fields),
        # falling back on the first
        select = Select(select_field)
        select_options = select.options
        selected_index = None
        for i, opt in enumerate(select_options):
            opt_text = opt.text.lower()
            if 'yes' in opt_text or 'ny' in opt_text or 'new york' in opt_text:
                selected_index = i
                break
        if selected_index is None:
            selected_index = min(2, len(select_options))
        select.select_by_index(selected_index)

    # submit the form
    if submit_button is not None:
        try:
            submit_button.click()  # trigger javascript events if possible
        except Exception:
            form.submit()  # fall back (e.g. if obscured by modal)
    elif text_field is not None:
        try:
            text_field.send_keys(Keys.RETURN)  # press enter
        except Exception:
            form.submit()  # fall back
    else:
        form.submit()

def _element_contains_text(element, text):
    """Scans various element attributes for the given text."""
    attributes = ['name', 'class', 'id', 'placeholder', 'value']
    text_list = text if type(text) is list else [text]
    for s in text_list:
        for attr in attributes:
            e = element.get_attribute(attr)
            if e is not None and s in e.lower():
                return True
    return False

def _type_in_field(input_field, text, clear):
    """Types text into an input field."""
    if clear:
        input_field.send_keys(Keys.CONTROL, 'a')
    input_field.send_keys(text)
