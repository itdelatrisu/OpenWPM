from automation import TaskManager, CommandSequence

# sites to crawl
# TODO: read from data/
NUM_BROWSERS = 1
sites = ['http://www.sweetwater.com/', 'https://www.ae.com/', 'http://www.officedepot.com/', 'http://www.gap.com/', 'https://www.jcrew.com/', 'http://www.gamestop.com/', 'http://www.cvs.com/', 'http://www.homedepot.com/', 'http://www.walmart.com']
api = 'http://lorveskel.me:8080/register'

# Loads the manager preference and 3 copies of the default browser dictionaries
manager_params, browser_params = TaskManager.load_default_params(NUM_BROWSERS)

# Update browser configuration (use this for per-browser settings)
for i in xrange(NUM_BROWSERS):
    browser_params[i]['headless'] = False
    browser_params[i]['bot_mitigation'] = False
    browser_params[i]['disable_flash'] = True
    browser_params[i]['disable_images'] = True

# Update TaskManager configuration (use this for crawl-wide settings)
manager_params['data_directory'] = '~/Desktop/'
manager_params['log_directory'] = '~/Desktop/'

# Instantiates the measurement platform
# Commands time out by default after 60 seconds
manager = TaskManager.TaskManager(manager_params, browser_params)

# Visits the sites with all browsers simultaneously
for site in sites:
    command_sequence = CommandSequence.CommandSequence(site)
    command_sequence.find_newsletters(api=api, num_links=4, timeout=120)
    manager.execute_command_sequence(command_sequence, index='**') # ** = synchronized browsers

# Shuts down the browsers and waits for the data to finish logging
manager.close()
