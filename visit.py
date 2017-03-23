from automation import TaskManager, CommandSequence
import sqlite3
from urllib2 import Request, urlopen, URLError
import json, os, time

# Constants
NUM_BROWSERS = 1
output_dir = '~/Desktop/'

# Loads the manager preference and 3 copies of the default browser dictionaries
manager_params, browser_params = TaskManager.load_default_params(NUM_BROWSERS)

# Update browser configuration (use this for per-browser settings)
for i in xrange(NUM_BROWSERS):
    browser_params[i]['headless'] = False
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
    command_sequence.get(sleep=1, timeout=60)
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
def get_crawl_id(db, url):
    with sqlite3.connect(db) as conn:
        sql = 'SELECT `crawl_id` FROM `CrawlHistory` WHERE `arguments` = ? ORDER BY `dtg` DESC LIMIT 1;'
        rows = conn.execute(sql, (url,)).fetchall()
        return None if not rows else rows[0][0]
def get_crawl_urls(db, crawl_id):
    with sqlite3.connect(db) as conn:
        sql = 'SELECT `url`, `referrer` FROM `http_requests` WHERE `crawl_id` = ?;'
        rows = conn.execute(sql, (crawl_id,)).fetchall()
        return rows

# Poll the mail API repeatedly
while True:
    data = api_get_sites()
    if data:
        # Got data, visit the sites
        requests = []
        for site in data['links']:
            # Visit the site (need new manager each time to finalize database entries)
            manager = TaskManager.TaskManager(manager_params, browser_params)
            crawl_site(site, manager)
            manager.close()

            # Parse the data
            crawl_id = get_crawl_id(db, site)
            if crawl_id:
                urls = get_crawl_urls(db, crawl_id)
                if urls:
                    requests.extend(urls)

        # Send the results back
        api_send_results(data['id'], requests)

    time.sleep(POLL_INTERVAL)
