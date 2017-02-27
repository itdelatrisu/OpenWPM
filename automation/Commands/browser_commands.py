from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import MoveTargetOutOfBoundsException
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
import os
import random
import time

from ..SocketInterface import clientsocket
from ..MPLogger import loggingclient
from utils.lso import get_flash_cookies
from utils.firefox_profile import get_cookies  # todo: add back get_localStorage,
from utils.webdriver_extensions import scroll_down, wait_until_loaded, get_intra_links

# Library for core WebDriver-based browser commands

NUM_MOUSE_MOVES = 10  # number of times to randomly move the mouse as part of bot mitigation
RANDOM_SLEEP_LOW = 1  # low end (in seconds) for random sleep times between page loads (bot mitigation)
RANDOM_SLEEP_HIGH = 7  # high end (in seconds) for random sleep times between page loads (bot mitigation)


def bot_mitigation(webdriver):
    """ performs three optional commands for bot-detection mitigation when getting a site """

    # bot mitigation 1: move the randomly around a number of times
    window_size = webdriver.get_window_size()
    num_moves = 0
    num_fails = 0
    while num_moves < NUM_MOUSE_MOVES + 1 and num_fails < NUM_MOUSE_MOVES:
        try:
            if num_moves == 0: #move to the center of the screen
                x = int(round(window_size['height']/2))
                y = int(round(window_size['width']/2))
            else: #move a random amount in some direction
                move_max = random.randint(0,500)
                x = random.randint(-move_max, move_max)
                y = random.randint(-move_max, move_max)
            action = ActionChains(webdriver)
            action.move_by_offset(x, y)
            action.perform()
            num_moves += 1
        except MoveTargetOutOfBoundsException:
            num_fails += 1
            #print "[WARNING] - Mouse movement out of bounds, trying a different offset..."
            pass

    # bot mitigation 2: scroll in random intervals down page
    scroll_down(webdriver)

    # bot mitigation 3: randomly wait so that page visits appear at irregular intervals
    time.sleep(random.randrange(RANDOM_SLEEP_LOW, RANDOM_SLEEP_HIGH))


def tab_restart_browser(webdriver):
    """
    kills the current tab and creates a new one to stop traffic
    note: this code if firefox-specific for now
    """
    if webdriver.current_url.lower() == 'about:blank':
        return

    switch_to_new_tab = ActionChains(webdriver)
    switch_to_new_tab.key_down(Keys.CONTROL).send_keys('t').key_up(Keys.CONTROL)
    switch_to_new_tab.key_down(Keys.CONTROL).send_keys(Keys.PAGE_UP).key_up(Keys.CONTROL)
    switch_to_new_tab.key_down(Keys.CONTROL).send_keys('w').key_up(Keys.CONTROL)
    switch_to_new_tab.perform()
    time.sleep(0.5)


def get_website(url, sleep, visit_id, webdriver, proxy_queue, browser_params, extension_socket):
    """
    goes to <url> using the given <webdriver> instance
    <proxy_queue> is queue for sending the proxy the current first party site
    """

    tab_restart_browser(webdriver)
    main_handle = webdriver.current_window_handle

    # sends top-level domain to proxy and extension (if enabled)
    # then, waits for it to finish marking traffic in proxy before moving to new site
    if proxy_queue is not None:
        proxy_queue.put(visit_id)
        while not proxy_queue.empty():
            time.sleep(0.001)
    if extension_socket is not None:
        extension_socket.send(visit_id)

    # Execute a get through selenium
    try:
        webdriver.get(url)
    except TimeoutException:
        pass

    # Sleep after get returns
    time.sleep(sleep)

    # Close modal dialog if exists
    try:
        WebDriverWait(webdriver, .5).until(EC.alert_is_present())
        alert = webdriver.switch_to_alert()
        alert.dismiss()
        time.sleep(1)
    except TimeoutException:
        pass

    # Close other windows (popups or "tabs")
    windows = webdriver.window_handles
    if len(windows) > 1:
        for window in windows:
            if window != main_handle:
                webdriver.switch_to_window(window)
                webdriver.close()
        webdriver.switch_to_window(main_handle)

    if browser_params['bot_mitigation']:
        bot_mitigation(webdriver)

def extract_links(webdriver, browser_params, manager_params):
    link_elements = webdriver.find_elements_by_tag_name('a')
    link_urls = set(element.get_attribute("href") for element in link_elements)

    sock = clientsocket()
    sock.connect(*manager_params['aggregator_address'])
    create_table_query = ("""
    CREATE TABLE IF NOT EXISTS links_found (
      found_on TEXT,
      location TEXT
    )
    """, ())
    sock.send(create_table_query)

    if len(link_urls) > 0:
        current_url = webdriver.current_url
        insert_query_string = """
        INSERT INTO links_found (found_on, location)
        VALUES (?, ?)
        """
        for link in link_urls:
            sock.send((insert_query_string, (current_url, link)))

    sock.close()

def browse_website(url, num_links, sleep, visit_id, webdriver, proxy_queue,
                   browser_params, manager_params, extension_socket):
    """Calls get_website before visiting <num_links> present on the page.

    Note: the site_url in the site_visits table for the links visited will
    be the site_url of the original page and NOT the url of the links visited.
    """
    # First get the site
    get_website(url, sleep, visit_id, webdriver, proxy_queue, browser_params, extension_socket)

    # Connect to logger
    logger = loggingclient(*manager_params['logger_address'])

    # Then visit a few subpages
    for i in range(num_links):
        links = get_intra_links(webdriver, url)
        links = filter(lambda x: x.is_displayed() == True, links)
        if len(links) == 0:
            break
        r = int(random.random()*len(links))
        logger.info("BROWSER %i: visiting internal link %s" % (browser_params['crawl_id'], links[r].get_attribute("href")))

        try:
            links[r].click()
            wait_until_loaded(webdriver, 300)
            time.sleep(max(1,sleep))
            if browser_params['bot_mitigation']:
                bot_mitigation(webdriver)
            webdriver.back()
            wait_until_loaded(webdriver, 300)
        except Exception:
            pass

def dump_flash_cookies(start_time, visit_id, webdriver, browser_params, manager_params):
    """ Save newly changed Flash LSOs to database

    We determine which LSOs to save by the `start_time` timestamp.
    This timestamp should be taken prior to calling the `get` for
    which creates these changes.
    """
    # Set up a connection to DataAggregator
    tab_restart_browser(webdriver)  # kills traffic so we can cleanly record data
    sock = clientsocket()
    sock.connect(*manager_params['aggregator_address'])

    # Flash cookies
    flash_cookies = get_flash_cookies(start_time)
    for cookie in flash_cookies:
        query = ("INSERT INTO flash_cookies (crawl_id, visit_id, domain, filename, local_path, \
                  key, content) VALUES (?,?,?,?,?,?,?)", (browser_params['crawl_id'], visit_id, cookie.domain,
                                                          cookie.filename, cookie.local_path,
                                                          cookie.key, cookie.content))
        sock.send(query)

    # Close connection to db
    sock.close()

def dump_profile_cookies(start_time, visit_id, webdriver, browser_params, manager_params):
    """ Save changes to Firefox's cookies.sqlite to database

    We determine which cookies to save by the `start_time` timestamp.
    This timestamp should be taken prior to calling the `get` for
    which creates these changes.

    Note that the extension's cookieInstrument is preferred to this approach,
    as this is likely to miss changes still present in the sqlite `wal` files.
    This will likely be removed in a future version.
    """
    # Set up a connection to DataAggregator
    tab_restart_browser(webdriver)  # kills traffic so we can cleanly record data
    sock = clientsocket()
    sock.connect(*manager_params['aggregator_address'])

    # Cookies
    rows = get_cookies(browser_params['profile_path'], start_time)
    if rows is not None:
        for row in rows:
            query = ("INSERT INTO profile_cookies (crawl_id, visit_id, baseDomain, name, value, \
                      host, path, expiry, accessed, creationTime, isSecure, isHttpOnly) \
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (browser_params['crawl_id'], visit_id) + row)
            sock.send(query)

    # Close connection to db
    sock.close()

def save_screenshot(screenshot_name, webdriver, browser_params, manager_params):
    webdriver.save_screenshot(os.path.join(manager_params['screenshot_path'], screenshot_name + '.png'))

def dump_page_source(dump_name, webdriver, browser_params, manager_params):
    with open(os.path.join(manager_params['source_dump_path'], dump_name + '.html'), 'wb') as f:
        f.write(webdriver.page_source.encode('utf8') + '\n')

#============================================================================================================

def find_newsletters(url, api, num_links, visit_id, webdriver, proxy_queue, browser_params,
                     manager_params, extension_socket):
    """Finds a newsletter form on the page. If not found, visits <num_links>
    internal links and scans those pages for a form. Submits the form if found.
    """
    # get the site
    get_website(url, 0, visit_id, webdriver, proxy_queue, browser_params, extension_socket)

    # connect to logger
    logger = loggingclient(*manager_params['logger_address'])

    # try to find newsletter form on landing page
    newsletter_form = _find_newsletter_form(webdriver)
    if newsletter_form is not None:
        #logger.info('form: %s', newsletter_form.get_attribute('outerHTML'))
        email = _get_email_from_api(api, webdriver, logger)
        _form_fill_and_submit(newsletter_form, email, webdriver)
        logger.info('submitted form on [%s] with email [%s]', webdriver.current_url, email)
        return

    # otherwise, scan more pages
    main_handle = webdriver.current_window_handle
    visited_links = set()
    for i in xrange(num_links):
        # get all links on the page
        #links = get_intra_links(webdriver, url)
        #links = filter(lambda x: x.is_displayed() == True, links)
        links = webdriver.find_elements_by_tag_name('a')

        # find a link to click
        next_link = None
        for link in links:
            # check if link is valid and not already visited
            href = link.get_attribute('href')
            if href is None:
                continue
            href = href.lower()
            if href in visited_links:
                continue

            # should we click this link?
            link_text = link.text.lower()
            if ('weekly ad' in link_text or 'newsletter' in link_text or
                'subscribe' in link_text or 'inbox' in link_text or
                'signup' in link_text or 'sign up' in link_text or
                'login' in link_text or 'log in' in link_text or
                'register' in link_text):
                next_link = link
                visited_links.add(href)
                break

        # no more links to click
        if next_link is None:
            break

        # click the link
        try:
            # load the page
            logger.info("clicking on link '%s' - %s" %
                        (next_link.text.lower(), next_link.get_attribute('href')))
            next_link.click()
            wait_until_loaded(webdriver, 5000)
            if browser_params['bot_mitigation']:
                bot_mitigation(webdriver)

            # find newsletter form
            newsletter_form = _find_newsletter_form(webdriver)
            if newsletter_form is not None:
                email = _get_email_from_api(api, webdriver, logger)
                _form_fill_and_submit(newsletter_form, email, webdriver)
                logger.info('submitted form on [%s] with email [%s]', webdriver.current_url, email)
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

from urllib import urlencode
from urllib2 import Request, urlopen, URLError
def _get_email_from_api(api, webdriver, logger):
    """Registers an email address with the mail API, and returns the email."""
    data = urlencode({
        'site': webdriver.title.encode('ascii', 'replace'),
        'url': webdriver.current_url,
    })
    req = Request(api, data)
    response = urlopen(req)
    return response.read()

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

def _form_fill_and_submit(form, email, webdriver):
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
            input_field.send_keys(email)
            text_field = input_field
        elif type == 'text':
            # try to decipher this based on field attributes
            if (_element_contains_text(input_field, 'email') or
                _element_contains_text(input_field, 'e-mail') or
                _element_contains_text(input_field, 'subscribe') or
                _element_contains_text(input_field, 'newsletter')):
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
            # TODO address/city/etc.
            elif _element_contains_text(input_field, 'search'):
                pass
            else:
                # default: assume email
                input_field.send_keys(email)
            text_field = input_field
        elif type == 'checkbox' or type == 'radio':
            # check anything/everything
            if not input_field.is_selected():
                input_field.click()
        elif type == 'password':
            input_field.send_keys('p4S$w0rd')
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
    time.sleep(0.5)  # TODO delete me
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
    wait_until_loaded(webdriver, 5000)
    time.sleep(4)  # TODO delete me
    # TODO check if we got redirected

def _element_contains_text(element, text):
    """Scans various element attributes for the given text."""
    attributes = ['name', 'class', 'id', 'placeholder', 'value']
    for attr in attributes:
        e = element.get_attribute(attr)
        if e is not None and text in e.lower():
            return True
    return False

#============================================================================================================
