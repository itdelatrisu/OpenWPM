from automation import TaskManager, CommandSequence

# Constants
NUM_BROWSERS = 1
output_dir = 'output_crawl/'
api = 'http://lorveskel.me:8080/register'
site_list = 'data/shopping-500.csv' #shopping-500.csv, news-500.csv, top-1m.csv, replica.csv
start_site_index = 0
def get_site(line):
    return 'http://' + line.strip().split(',')[1] if line.count(',') >= 1 else None
    # return line

# Loads the manager preference and the default browser dictionaries
manager_params, browser_params = TaskManager.load_default_params(NUM_BROWSERS)

# Update browser configuration (use this for per-browser settings)
for i in xrange(NUM_BROWSERS):
    browser_params[i]['headless'] = False
    browser_params[i]['bot_mitigation'] = True
    browser_params[i]['disable_flash'] = True
    browser_params[i]['disable_images'] = True

# Update TaskManager configuration (use this for crawl-wide settings)
manager_params['data_directory'] = output_dir
manager_params['log_directory'] = output_dir
manager_params['database_name'] = 'crawl.sqlite'

# Instantiates the measurement platform
# Commands time out by default after 60 seconds
manager = TaskManager.TaskManager(manager_params, browser_params)

# Visits the sites with all browsers simultaneously
def crawl_site(site, manager, api):
    command_sequence = CommandSequence.CommandSequence(site)
    command_sequence.find_newsletters(api=api, num_links=3, timeout=120)
    manager.execute_command_sequence(command_sequence, index='**') # ** = synchronized browsers

# Read site list
index = 0
with open(site_list) as f:
    for line in f:
        index += 1
        if index < start_site_index:
            continue
        site = get_site(line)
        if site is not None:
            crawl_site(site, manager, api)

# Shuts down the browsers and waits for the data to finish logging
manager.close()
