from automation import TaskManager, CommandSequence
import sqlite3
from urllib2 import Request, urlopen, URLError
import copy, json, os, time

# Constants
NUM_BROWSERS = 1
output_dir = 'output_visit/'

# Loads the manager preference and the default browser dictionaries
manager_params, browser_params = TaskManager.load_default_params(NUM_BROWSERS)

# Update browser configuration (use this for per-browser settings)
for i in xrange(NUM_BROWSERS):
    browser_params[i]['headless'] = True
    browser_params[i]['bot_mitigation'] = True
    browser_params[i]['disable_flash'] = True
    browser_params[i]['disable_images'] = False
    browser_params[i]['http_instrument'] = True

# Update TaskManager configuration (use this for crawl-wide settings)
manager_params['data_directory'] = output_dir
manager_params['log_directory'] = output_dir
manager_params['database_name'] = 'visit.sqlite'

# Visits the sites with all browsers simultaneously
def crawl_site(site, manager):
    command_sequence = CommandSequence.CommandSequence(site)
    command_sequence.get(sleep=1, timeout=120)
    manager.execute_command_sequence(command_sequence, index='**') # ** = synchronized browsers

# Mail API functions
api = 'http://lorveskel.me:8080/'
api_visit = api + 'visit'
api_results = api + 'results'
POLL_INTERVAL = 10  # in seconds
def api_get_sites():
    try:
        req = Request(api_visit)
        f = urlopen(req)
        response = f.read()
        f.close()
        return json.loads(response)
    except:
        return {}
def api_send_results(id, requests):
    try:
        data = json.dumps({'id': id, 'requests': requests})
        req = Request(api_results, data, {'Content-Type': 'application/json'})
        f = urlopen(req)
        response = f.read()
        f.close()
    except:
        pass

# Database functions
db = os.path.expanduser(os.path.join(manager_params['data_directory'], manager_params['database_name']))
def get_connection(db):
    return sqlite3.connect(db)
def get_crawl_id(conn, url):
    sql = 'SELECT `crawl_id` FROM `CrawlHistory` WHERE `arguments` = ? ORDER BY `dtg` DESC LIMIT 1;'
    rows = conn.execute(sql, (url,)).fetchall()
    return None if not rows else rows[0][0]
def get_crawl_urls(conn, crawl_id):
    sql = 'SELECT `url`, `top_level_url`, `referrer`, `post_body` FROM `http_requests` WHERE `crawl_id` = ?;'
    return conn.execute(sql, (crawl_id,)).fetchall()

# Poll the mail API repeatedly
while True:
    data = api_get_sites()
    if data:
        # Got data, visit the sites
        print('Got %d links for ID %d.' % (len(data['links']), data['id']))
        requests = []
        for site in data['links']:
            # Visit the site (need new manager each time to finalize database entries)
            manager = TaskManager.TaskManager(copy.deepcopy(manager_params), copy.deepcopy(browser_params))
            crawl_site(site, manager)
            manager.close()

            # Parse the data
            conn = get_connection(db)
            crawl_id = get_crawl_id(conn, site)
            if crawl_id:
                urls = get_crawl_urls(conn, crawl_id)
                if urls:
                    requests.extend(urls)
            conn.close()

            # Clean up
            os.remove(db)

        # Send the results back
        api_send_results(data['id'], requests)
        print('Sent %d results for ID %d.' % (len(requests), data['id']))

    time.sleep(POLL_INTERVAL)
