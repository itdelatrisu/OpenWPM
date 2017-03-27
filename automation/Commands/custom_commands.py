from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
from urllib import urlencode
from urllib2 import Request, urlopen, URLError
import random
import time

from ..MPLogger import loggingclient
from utils.webdriver_extensions import wait_until_loaded, get_intra_links
from browser_commands import get_website

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
    link_text_rank = [
        ('weekly ad', 10),
        ('newsletter', 10),
        ('subscribe', 9),
        ('inbox', 8),
        ('signup', 5),
        ('sign up', 5),
        ('register', 4),
        ('create', 4),
    ]
    visited_links = set()
    for i in xrange(num_links):
        # get all links on the page
        #links = get_intra_links(webdriver, url)
        #links = filter(lambda x: x.is_displayed() == True, links)
        links = webdriver.find_elements_by_tag_name('a')

        # find links to click
        match_links = []
        for link in links:
            # check if link is valid and not already visited
            href = link.get_attribute('href')
            if href is None or href in visited_links:
                continue

            # should we click this link?
            link_text = link.text.lower()
            rank = 0
            for s, v in link_text_rank:
                if s in link_text:
                    rank = v
                    match_links.append((link, v, link_text, href))
                    break
            if rank > 5:  # good enough, stop looking
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
            wait_until_loaded(webdriver, 5000)
            if browser_params['bot_mitigation']:
                bot_mitigation(webdriver)

            # find newsletter form
            if _find_and_fill_form(webdriver, api, logger):
                return

            # go back
            webdriver.back()
            wait_until_loaded(webdriver, 5000)

            # close other windows (popups or "tabs")
            windows = webdriver.window_handles
            if len(windows) > 1:
                for window in windows:
                    if window != main_handle:
                        webdriver.switch_to_window(window)
                        webdriver.close()
                webdriver.switch_to_window(main_handle)
                time.sleep(1)
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

def _find_and_fill_form(webdriver, api, logger):
    """Finds and fills a form, and returns True if accomplished."""
    # try to find newsletter form on landing page
    newsletter_form = _find_newsletter_form(webdriver)
    if newsletter_form is None:
        return False

    current_url = webdriver.current_url
    email = _get_email_from_api(api, webdriver, logger)
    _form_fill_and_submit(newsletter_form, email, webdriver)
    logger.info('submitted form on [%s] with email [%s]', current_url, email)

    # fill any follow-up forms
    wait_until_loaded(webdriver, 5000)  # wait if we got redirected
    follow_up_form = _find_newsletter_form(webdriver)
    if follow_up_form is not None:
        _form_fill_and_submit(follow_up_form, email, webdriver, current_url != webdriver.current_url)

    return True

def _find_newsletter_form(webdriver):
    """Tries to find a form element on the page for newsletter sign-up.
    Returns None if no form was found.
    """
    forms = webdriver.find_elements_by_tag_name('form')
    for form in forms:
        if not form.is_displayed():
            continue

        # find words 'email' or 'newsletter' in the form
        form_html = form.get_attribute('outerHTML').lower()
        if 'email' in form_html or 'newsletter' in form_html:
            # check if an input field contains an email element
            input_fields = form.find_elements_by_tag_name('input')
            for input_field in input_fields:
                type = input_field.get_attribute('type').lower()
                if type == 'email':
                    return form
                elif type == 'text':
                    if (_element_contains_text(input_field, 'email') or
                        _element_contains_text(input_field, 'e-mail') or
                        _element_contains_text(input_field, 'subscribe') or
                        _element_contains_text(input_field, 'newsletter')):
                        return form
    return None

def _form_fill_and_submit(form, email, webdriver, ignore_nonempty_email=False):
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
            if not ignore_nonempty_email or email not in input_field.get_attribute('value'):
                input_field.send_keys(email)
            text_field = input_field
        elif type == 'text':
            # try to decipher this based on field attributes
            if (_element_contains_text(input_field, 'email') or
                _element_contains_text(input_field, 'e-mail') or
                _element_contains_text(input_field, 'subscribe') or
                _element_contains_text(input_field, 'newsletter')):
                if not ignore_nonempty_email or email not in input_field.get_attribute('value'):
                    input_field.send_keys(email)
            elif _element_contains_text(input_field, 'name'):
                if (_element_contains_text(input_field, 'user') or
                    _element_contains_text(input_field, 'account')):
                    input_field.send_keys(fake_user)
                elif _element_contains_text(input_field, 'first'):
                    input_field.send_keys('Bob')
                elif _element_contains_text(input_field, 'last'):
                    input_field.send_keys('Smith')
                elif _element_contains_text(input_field, 'company'):
                    input_field.send_keys('Smith & Co.')
                else:
                    input_field.send_keys('Bob Smith')
            elif (_element_contains_text(input_field, 'phone') or
                  _element_contains_text(input_field, 'tel') or
                  _element_contains_text(input_field, 'mobile')):
                input_field.send_keys(fake_tel)
            elif (_element_contains_text(input_field, 'zip') or
                  _element_contains_text(input_field, 'postal')):
                input_field.send_keys('12345')
            elif (_element_contains_text(input_field, 'street') or
                  _element_contains_text(input_field, 'address')):
                if (_element_contains_text(input_field, '2') or
                    _element_contains_text(input_field, 'number')):
                    input_field.send_keys('Apt. 101')
                elif _element_contains_text(input_field, '3'):
                    pass
                else:
                    input_field.send_keys('101 Main St.')
            elif _element_contains_text(input_field, 'city'):
                input_field.send_keys('Schenectady')
            elif _element_contains_text(input_field, 'search'):
                pass
            else:
                # default: assume email
                if not ignore_nonempty_email or email not in input_field.get_attribute('value'):
                    input_field.send_keys(email)
            text_field = input_field
        elif type == 'checkbox' or type == 'radio':
            # check anything/everything
            if not input_field.is_selected():
                input_field.click()
        elif type == 'password':
            input_field.send_keys('p4S$w0rd123')
        elif type == 'tel':
            input_field.send_keys(fake_tel)
        elif type == 'submit' or type == 'button' or type == 'image':
            if (_element_contains_text(input_field, 'submit') or
                _element_contains_text(input_field, 'sign up')):
                submit_button = input_field
        elif type == 'reset' or type == 'hidden' or type == 'search':
            # common irrelevant input types
            pass
        else:
            # default: assume email
            if not ignore_nonempty_email or email not in input_field.get_attribute('value'):
                input_field.send_keys(email)

    # fill in 'select' fields
    select_fields = form.find_elements_by_tag_name('select')
    for select_field in select_fields:
        if not select_field.is_displayed():
            continue

        # select select element if possible, otherwise first
        select = Select(select_field)
        selected_index = None
        for index in range(len(select.options)):
            if selected_index is None:
                selected_index = index
            else:
                selected_index = index
                break
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
    for attr in attributes:
        e = element.get_attribute(attr)
        if e is not None and text in e.lower():
            return True
    return False
