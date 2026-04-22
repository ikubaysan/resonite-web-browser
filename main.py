from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options

# Path to geckodriver (change if needed)
GECKODRIVER_PATH = r"geckodriver.exe"

# Firefox options
options = Options()
options.headless = False  # Set to True if you want it hidden

# Create service
service = Service(GECKODRIVER_PATH)

# Start browser
driver = webdriver.Firefox(service=service, options=options)

try:
    # Open webpage
    url = "https://stackoverflow.com/questions/73298355/how-to-remove-duplicate-values-in-one-column-but-keep-the-rows-pandas"
    driver.get(url)

    # Take full-page screenshot
    driver.save_full_page_screenshot("fullpage_firefox.png")

    print("Full-page screenshot saved as fullpage_firefox.png")

finally:
    driver.quit()