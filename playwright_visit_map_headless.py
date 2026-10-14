import asyncio
from playwright.async_api import async_playwright
import os
import requests
import sys
import argparse
import difflib


def create_search_directory(search_text):
    """Create directory structure for search results"""
    # Clean the search text for use as folder name
    clean_search = "".join(c for c in search_text if c.isalnum() or c in (' ', '-', '_')).rstrip()
    clean_search = clean_search.replace(' ', '_')
    
    # Create the directory path
    search_dir = os.path.join("result_pdfs", clean_search)
    
    # Create directories if they don't exist
    os.makedirs(search_dir, exist_ok=True)
    
    return search_dir


async def run_browser(search_text, headless):
    url = "https://oss.uredjenazemlja.hr"
    cache_dir = os.path.abspath("./playwright_cache")
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            cache_dir,
            headless=headless,
            args=['--no-sandbox']
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        
        try:
            print(f"Loading main page: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            print("✓ Main page loaded successfully")
        except Exception as e:
            print(f"✗ ERROR: Could not load main page - {e}")
            await browser.close()
            return
        
        try:
            print(f"Searching for: '{search_text}'")
            await page.wait_for_selector('input#floatingInput', timeout=20000)
            
            # Type the entire search text at once (faster than character by character)
            await page.fill('input#floatingInput', search_text)
            print("✓ Entered search text")
            
            # Wait a moment for suggestions to load
            await page.wait_for_timeout(1500)
            
            # Wait for suggestions with better error handling
            try:
                await page.wait_for_selector('.mat-autocomplete-panel .mat-option', timeout=10000)
                options = await page.locator('.mat-autocomplete-panel .mat-option').all()
                if not options:
                    print(f"✗ No search results found for '{search_text}'. Please try a different search term.")
                    await browser.close()
                    return
                
                print(f"✓ Found {len(options)} search suggestions")
                
                # Get all option texts
                option_texts = []
                for option in options:
                    text = await option.text_content()
                    option_texts.append(text.strip() if text else '')
                
                # Check for exact match first
                selected_option = None
                selected_index = 0
                match_type = "first"
                
                search_lower = search_text.strip().lower()
                
                # 1. Check for exact match
                for i, option_text in enumerate(option_texts):
                    if option_text.lower() == search_lower:
                        selected_option = options[i]
                        selected_index = i
                        match_type = "exact"
                        print(f"✓ Found exact match: '{option_text}'")
                        break
                
                # 2. If no exact match, try fuzzy matching
                if selected_option is None:
                    # Calculate similarity ratios for all options
                    similarities = []
                    for i, option_text in enumerate(option_texts):
                        ratio = difflib.SequenceMatcher(None, search_lower, option_text.lower()).ratio()
                        similarities.append((ratio, i, option_text))
                    
                    # Sort by similarity ratio (highest first)
                    similarities.sort(reverse=True, key=lambda x: x[0])
                    
                    # Use the most similar option if it's above a threshold (0.6)
                    best_ratio, best_index, best_text = similarities[0]
                    if best_ratio >= 0.6:
                        selected_option = options[best_index]
                        selected_index = best_index
                        match_type = "fuzzy"
                        print(f"✓ Found fuzzy match ({best_ratio:.2%} similarity): '{best_text}'")
                    else:
                        # If no good fuzzy match, use first option
                        selected_option = options[0]
                        selected_index = 0
                        match_type = "first"
                        print(f"✓ No good match found (best: {best_ratio:.2%}), selecting first suggestion: '{option_texts[0] if option_texts[0] else 'N/A'}'")
                
                await selected_option.click()
                print(f"✓ Selected suggestion #{selected_index + 1} ({match_type} match)")
                
            except Exception as e:
                print(f"✗ No search suggestions appeared for '{search_text}'. The search term might be invalid.")
                await browser.close()
                return
                
            print("Waiting 2 seconds for map to load...")
            await page.wait_for_timeout(2000)
            
        except Exception as e:
            print(f"✗ Error during search process: {e}")
            await browser.close()
            return

        # Set up network monitoring BEFORE clicking map
        parcel_info_url = None
        lr_units_request = None  # For lr/lr-units endpoint (plural)
        lr_unit_singular_request = None  # For lr/lr-unit endpoint (singular)
        
        def handle_response(response):
            nonlocal parcel_info_url, lr_units_request, lr_unit_singular_request
            if "parcel-info" in response.url:
                parcel_info_url = response.url
                print(f"✓ Captured parcel-info URL: {response.url}")
            elif "lr/lr-units" in response.url:
                lr_units_request = {
                    'url': response.url,
                    'method': response.request.method,
                    'post_data': response.request.post_data
                }
                print(f"✓ Captured LR units request: {response.request.method} {response.url}")
            elif "lr/lr-unit" in response.url:
                # Parse URL parameters
                url_parts = response.url.split('?')
                get_params = url_parts[1] if len(url_parts) > 1 else None
                
                # Extract lrUnitNumber and mainBookId from URL
                lr_unit_number_from_url = None
                main_book_id = None
                if get_params:
                    params = get_params.split('&')
                    for param in params:
                        if param.startswith('lrUnitNumber='):
                            lr_unit_number_from_url = param.split('=')[1]
                        elif param.startswith('mainBookId='):
                            main_book_id = param.split('=')[1]
                
                lr_unit_singular_request = {
                    'url': response.url,
                    'method': response.request.method,
                    'get_params': get_params,
                    'lrUnitNumber': lr_unit_number_from_url,
                    'mainBookId': main_book_id
                }
                print(f"✓ Captured lr/lr-unit (singular) request: {response.request.method} {response.url}")
                if lr_unit_number_from_url and main_book_id:
                    print(f"  - lrUnitNumber from URL: {lr_unit_number_from_url}, mainBookId: {main_book_id}")
        
        def handle_request(request):
            nonlocal lr_units_request, lr_unit_singular_request
            if "lr/lr-units" in request.url and request.method == "POST":
                lr_units_request = {
                    'url': request.url,
                    'method': request.method,
                    'post_data': request.post_data
                }
                print(f"✓ Captured LR units POST request: {request.method} {request.url}")
            elif "lr/lr-unit" in request.url and request.method == "GET":
                # Parse URL parameters
                url_parts = request.url.split('?')
                get_params = url_parts[1] if len(url_parts) > 1 else None
                
                # Extract lrUnitNumber and mainBookId from URL
                lr_unit_number_from_url = None
                main_book_id = None
                if get_params:
                    params = get_params.split('&')
                    for param in params:
                        if param.startswith('lrUnitNumber='):
                            lr_unit_number_from_url = param.split('=')[1]
                        elif param.startswith('mainBookId='):
                            main_book_id = param.split('=')[1]
                
                lr_unit_singular_request = {
                    'url': request.url,
                    'method': request.method,
                    'get_params': get_params,
                    'lrUnitNumber': lr_unit_number_from_url,
                    'mainBookId': main_book_id
                }
                print(f"✓ Captured lr/lr-unit (singular) GET request: {request.method} {request.url}")
                if lr_unit_number_from_url and main_book_id:
                    print(f"  - lrUnitNumber from URL: {lr_unit_number_from_url}, mainBookId: {main_book_id}")
        
        # Set up network monitoring
        page.on("response", handle_response)
        page.on("request", handle_request)
        
        print("✓ Network monitoring activated")
        
        # Click on map center to select parcel
        print("Attempting to click map center to select parcel...")
        selectors = ['div.leaflet-container', '#map', 'canvas']
        clicked = False
        for selector in selectors:
            try:
                box = await page.locator(selector).bounding_box()
                if box:
                    center_x = box['x'] + box['width'] / 2
                    center_y = box['y'] + box['height'] / 2
                    await page.mouse.click(center_x, center_y)
                    print(f"✓ Clicked map center using selector: {selector}")
                    clicked = True
                    break
            except Exception:
                continue
        
        if not clicked:
            print("✗ ERROR: Could not click map center with any selector.")
            await browser.close()
            return
        
        print("Waiting 5 seconds for map selection to process and capture network requests...")
        await page.wait_for_timeout(5000)
        
        # Click the "Zemljišnoknjižni podaci" button to trigger LR units request
        try:
            print("Looking for land registry data button...")
            lr_button = page.locator('span:has-text("Zemljišnoknjižni podaci")')
            await lr_button.wait_for(timeout=10000)
            print("✓ Found land registry data button, clicking...")
            await lr_button.click()
            
            # Wait for the LR units network request
            print("Waiting for land registry network requests...")
            await asyncio.sleep(3)
            
        except Exception as e:
            print(f"⚠ Warning: Could not find land registry data button - {e}")
            print("Continuing with available data...")
        
        if parcel_info_url:
            print(f"✓ Found parcel data, processing...")
            
            # Create directory for this search
            search_dir = create_search_directory(search_text)
            print(f"✓ Created directory: {search_dir}")
            
            # Extract parcel ID for later use
            try:
                parcel_id = parcel_info_url.split('parcelId=')[1]
                print(f"✓ Extracted parcel ID: {parcel_id}")
            except Exception:
                print("✗ Error: Could not extract parcel ID from URL")
                await browser.close()
                return
            
            # Make request to parcel-info API
            try:
                # Get cookies from the browser context
                cookies = await page.context.cookies()
                cookie_dict = {cookie.get('name', ''): cookie.get('value', '') for cookie in cookies}
                
                headers = {
                    'accept': 'application/json, text/plain, */*',
                    'accept-encoding': 'gzip, deflate, br, zstd',
                    'accept-language': 'hr',
                    'cache-control': 'no-cache',
                    'pragma': 'no-cache',
                    'referer': 'https://oss.uredjenazemlja.hr/map',
                    'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                    'sec-fetch-dest': 'empty',
                    'sec-fetch-mode': 'cors',
                    'sec-fetch-site': 'same-origin',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
                }
                
                response = requests.get(parcel_info_url, headers=headers, cookies=cookie_dict)
                
                if response.status_code != 200:
                    print(f"✗ Error: Parcel info API request failed (Status: {response.status_code})")
                    await browser.close()
                    return
                
                print("✓ Retrieved parcel information")
                
                try:
                    json_data = response.json()
                except Exception as e:
                    print(f"✗ Error: Could not parse parcel info response - {e}")
                    await browser.close()
                    return
                        
                try:
                    json_data = response.json()
                except Exception as e:
                    print(f"✗ Error: Could not parse parcel info response - {e}")
                    await browser.close()
                    return
                
                # Extract possessionSheetId from the response
                possession_sheet_ids = []
                
                # Check in parcelParts
                if 'parcelParts' in json_data:
                    for part in json_data['parcelParts']:
                        if 'possessionSheetId' in part:
                            possession_sheet_ids.append(part['possessionSheetId'])
                
                # Check in possessionSheets
                if 'possessionSheets' in json_data:
                    for sheet in json_data['possessionSheets']:
                        if 'possessionSheetId' in sheet:
                            possession_sheet_ids.append(sheet['possessionSheetId'])
                
                # Remove duplicates
                unique_possession_sheet_ids = list(set(possession_sheet_ids))
                
                if not unique_possession_sheet_ids:
                    print("⚠ Warning: No possession sheet IDs found in parcel data")
                else:
                    print(f"✓ Found {len(unique_possession_sheet_ids)} possession sheet(s)")
                
                # Process possession sheet extract if available
                if unique_possession_sheet_ids:
                    possession_sheet_id = unique_possession_sheet_ids[0]
                    await download_possession_sheet_pdf(possession_sheet_id, headers, cookie_dict, search_text, search_dir)
                
                # Process cadastral plan extract
                await download_cadastral_plan_pdf(parcel_id, headers, cookie_dict, search_text, search_dir)
                
                # Process land registry extract
                await process_land_registry_data(lr_units_request, lr_unit_singular_request, json_data, parcel_id, headers, cookie_dict, search_text, search_dir)
                
            except Exception as e:
                print(f"✗ Error making parcel info API request: {e}")
        else:
            print("✗ Error: No parcel information found. The selected area might not have parcel data.")
            
            # Fallback: Try to manually construct parcel-info URL
            print("Attempting fallback approach...")
            try:
                # Wait a bit more and try to find parcel info in the page's network activity
                await asyncio.sleep(2)
                
                # Try clicking on the map again to trigger more network requests
                print("Trying additional map click to trigger network requests...")
                box = await page.locator('div.leaflet-container').bounding_box()
                if box:
                    # Try clicking slightly off-center
                    await page.mouse.click(box['x'] + box['width'] / 2 + 10, box['y'] + box['height'] / 2 + 10)
                    await asyncio.sleep(3)
                    
                    if parcel_info_url:
                        print(f"✓ Found parcel data after second click: {parcel_info_url}")
                        # Retry the processing
                        search_dir = create_search_directory(search_text)
                        print(f"✓ Created directory: {search_dir}")
                        
                        parcel_id = parcel_info_url.split('parcelId=')[1]
                        print(f"✓ Extracted parcel ID: {parcel_id}")
                        
                        # Continue with the API processing (copy the same logic from above)
                        cookies = await page.context.cookies()
                        cookie_dict = {cookie.get('name', ''): cookie.get('value', '') for cookie in cookies}
                        
                        headers = {
                            'accept': 'application/json, text/plain, */*',
                            'accept-encoding': 'gzip, deflate, br, zstd',
                            'accept-language': 'hr',
                            'cache-control': 'no-cache',
                            'pragma': 'no-cache',
                            'referer': 'https://oss.uredjenazemlja.hr/map',
                            'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
                            'sec-ch-ua-mobile': '?0',
                            'sec-ch-ua-platform': '"Windows"',
                            'sec-fetch-dest': 'empty',
                            'sec-fetch-mode': 'cors',
                            'sec-fetch-site': 'same-origin',
                            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
                        }
                        
                        response = requests.get(parcel_info_url, headers=headers, cookies=cookie_dict)
                        
                        if response.status_code == 200:
                            print("✓ Retrieved parcel information from fallback")
                            json_data = response.json()
                            
                            # Extract possessionSheetId from the response
                            possession_sheet_ids = []
                            
                            # Check in parcelParts
                            if 'parcelParts' in json_data:
                                for part in json_data['parcelParts']:
                                    if 'possessionSheetId' in part:
                                        possession_sheet_ids.append(part['possessionSheetId'])
                            
                            # Check in possessionSheets
                            if 'possessionSheets' in json_data:
                                for sheet in json_data['possessionSheets']:
                                    if 'possessionSheetId' in sheet:
                                        possession_sheet_ids.append(sheet['possessionSheetId'])
                            
                            # Remove duplicates
                            unique_possession_sheet_ids = list(set(possession_sheet_ids))
                            
                            if not unique_possession_sheet_ids:
                                print("⚠ Warning: No possession sheet IDs found in parcel data")
                            else:
                                print(f"✓ Found {len(unique_possession_sheet_ids)} possession sheet(s)")
                            
                            # Process possession sheet extract if available
                            if unique_possession_sheet_ids:
                                possession_sheet_id = unique_possession_sheet_ids[0]
                                await download_possession_sheet_pdf(possession_sheet_id, headers, cookie_dict, search_text, search_dir)
                            
                            # Process cadastral plan extract
                            await download_cadastral_plan_pdf(parcel_id, headers, cookie_dict, search_text, search_dir)
                            
                            # Process land registry extract
                            await process_land_registry_data(lr_units_request, lr_unit_singular_request, json_data, parcel_id, headers, cookie_dict, search_text, search_dir)
                        else:
                            print(f"✗ Fallback also failed (Status: {response.status_code})")
                    else:
                        print("✗ Fallback approach also failed to find parcel data")
                        
            except Exception as e:
                print(f"✗ Error in fallback approach: {e}")
        
        await browser.close()


async def download_possession_sheet_pdf(possession_sheet_id, headers, cookie_dict, search_text, search_dir):
    """Download possession sheet PDF"""
    pdf_extract_url = f"https://oss.uredjenazemlja.hr/oss/public/reports/get-possessionsheet-extract?possessionSheetId={possession_sheet_id}"
    
    try:
        print("Downloading possession sheet extract...")
        pdf_response = requests.get(pdf_extract_url, headers=headers, cookies=cookie_dict)
        
        if pdf_response.status_code == 200:
            try:
                pdf_data = pdf_response.json()
                
                # Download the PDF from the fileUrl
                if pdf_data and len(pdf_data) > 0 and 'fileUrl' in pdf_data[0]:
                    file_url = pdf_data[0]['fileUrl']
                    
                    # Construct the full URL correctly
                    if file_url.startswith('/reports/'):
                        full_pdf_url = f"https://oss.uredjenazemlja.hr/oss/public{file_url}"
                    elif file_url.startswith('/'):
                        full_pdf_url = f"https://oss.uredjenazemlja.hr{file_url}"
                    else:
                        full_pdf_url = f"https://oss.uredjenazemlja.hr/oss/public/{file_url}"
                    
                    pdf_file_response = requests.get(full_pdf_url, headers=headers, cookies=cookie_dict)
                    
                    if pdf_file_response.status_code == 200:
                        filename = file_url.split('/')[-1]
                        save_path = os.path.join(search_dir, f"PossessionSheet_{filename}")
                        
                        with open(save_path, 'wb') as f:
                            f.write(pdf_file_response.content)
                        print(f"✓ Downloaded: {save_path}")
                    else:
                        print(f"✗ Failed to download possession sheet PDF (Status: {pdf_file_response.status_code})")
                else:
                    print("⚠ No possession sheet PDF available for download")
                    
            except Exception as e:
                print(f"✗ Error processing possession sheet response: {e}")
        else:
            print(f"✗ Possession sheet API request failed (Status: {pdf_response.status_code})")
            
    except Exception as e:
        print(f"✗ Error downloading possession sheet PDF: {e}")


async def download_cadastral_plan_pdf(parcel_id, headers, cookie_dict, search_text, search_dir):
    """Download cadastral plan PDF"""
    kp_extract_url = f"https://oss.uredjenazemlja.hr/oss/public/reports/get-kp-extract/{parcel_id}"
    
    try:
        print("Downloading cadastral plan extract...")
        kp_response = requests.get(kp_extract_url, headers=headers, cookies=cookie_dict)
        
        if kp_response.status_code == 200:
            try:
                kp_data = kp_response.json()
                
                if kp_data and 'fileUrl' in kp_data:
                    file_url = kp_data['fileUrl']
                    
                    # Construct the full URL correctly
                    if file_url.startswith('/reports/'):
                        full_pdf_url = f"https://oss.uredjenazemlja.hr/oss/public{file_url}"
                    elif file_url.startswith('/'):
                        full_pdf_url = f"https://oss.uredjenazemlja.hr{file_url}"
                    else:
                        full_pdf_url = f"https://oss.uredjenazemlja.hr/oss/public/{file_url}"
                    
                    kp_pdf_response = requests.get(full_pdf_url, headers=headers, cookies=cookie_dict)
                    
                    if kp_pdf_response.status_code == 200:
                        filename = file_url.split('/')[-1]
                        save_path = os.path.join(search_dir, f"CadastralPlan_{filename}")
                        
                        with open(save_path, 'wb') as f:
                            f.write(kp_pdf_response.content)
                        print(f"✓ Downloaded: {save_path}")
                    else:
                        print(f"✗ Failed to download cadastral plan PDF (Status: {kp_pdf_response.status_code})")
                else:
                    print("⚠ No cadastral plan PDF available for download")
                    
            except Exception as e:
                print(f"✗ Error processing cadastral plan response: {e}")
        else:
            print(f"✗ Cadastral plan API request failed (Status: {kp_response.status_code})")
            
    except Exception as e:
        print(f"✗ Error downloading cadastral plan PDF: {e}")


async def process_land_registry_data(lr_units_request, lr_unit_singular_request, json_data, parcel_id, headers, cookie_dict, search_text, search_dir):
    """Process land registry data and download LDB extract PDF"""
    lr_unit_id = None
    
    # Try using captured lr/lr-unit (singular) request first (new endpoint)
    if lr_unit_singular_request:
        print("Processing land registry data from captured lr/lr-unit (singular) request...")
        try:
            lr_response = requests.get(lr_unit_singular_request['url'], headers=headers, cookies=cookie_dict)
            
            if lr_response.status_code == 200:
                lr_data = lr_response.json()
                print(f"✓ lr/lr-unit (singular) response received (Status: {lr_response.status_code})")
                
                # Handle both array and single object responses
                if isinstance(lr_data, list) and len(lr_data) > 0:
                    # Response is an array, check first element
                    if 'lrUnitId' in lr_data[0]:
                        lr_unit_id = lr_data[0]['lrUnitId']
                        print(f"✓ Retrieved lrUnitId: {lr_unit_id} from lr/lr-unit (singular) endpoint")
                    else:
                        print("⚠ No lrUnitId found in lr/lr-unit (singular) response")
                elif isinstance(lr_data, dict) and 'lrUnitId' in lr_data:
                    # Response is a single object
                    lr_unit_id = lr_data['lrUnitId']
                    print(f"✓ Retrieved lrUnitId: {lr_unit_id} from lr/lr-unit (singular) endpoint")
                else:
                    print("⚠ No lrUnitId found in lr/lr-unit (singular) response")
            else:
                print(f"✗ lr/lr-unit (singular) request failed (Status: {lr_response.status_code})")
                print(f"✗ lr/lr-unit (singular) response: {lr_response.text}")
            
        except Exception as e:
            print(f"⚠ Warning: Error with captured lr/lr-unit (singular) request: {e}")
    
    # Try using captured lr/lr-units (plural) request second
    if not lr_unit_id and lr_units_request:
        print("Processing land registry data from captured lr/lr-units (plural) request...")
        try:
            if lr_units_request['method'] == 'POST' and lr_units_request['post_data']:
                import json as json_module
                post_data = json_module.loads(lr_units_request['post_data'])
                lr_response = requests.post(lr_units_request['url'], headers=headers, cookies=cookie_dict, json=post_data)
            else:
                lr_response = requests.get(lr_units_request['url'], headers=headers, cookies=cookie_dict)
            
            if lr_response.status_code == 200:
                lr_data = lr_response.json()
                print(f"✓ lr/lr-units (plural) response received (Status: {lr_response.status_code})")
                
                if lr_data and len(lr_data) > 0 and 'lrUnitId' in lr_data[0]:
                    lr_unit_id = lr_data[0]['lrUnitId']
                    print(f"✓ Retrieved lrUnitId: {lr_unit_id} from lr/lr-units (plural) endpoint")
                else:
                    print("⚠ No lrUnitId found in lr/lr-units (plural) response")
            else:
                print(f"✗ lr/lr-units (plural) request failed (Status: {lr_response.status_code})")
                print(f"✗ lr/lr-units (plural) response: {lr_response.text}")
            
        except Exception as e:
            print(f"⚠ Warning: Error with captured lr/lr-units (plural) request: {e}")
    
    # Fallback: try manual POST request to lr/lr-units (plural)
    if not lr_unit_id:
        print("Attempting fallback land registry request to lr/lr-units (plural)...")
        try:
            lr_units_url = "https://oss.uredjenazemlja.hr/oss/public/lr/lr-units"
            
            # Try to extract lrUnitId from the parcel-info response
            if 'lrUnitsFromParcelLinks' in json_data and len(json_data['lrUnitsFromParcelLinks']) > 0:
                lr_unit_id_from_parcel = json_data['lrUnitsFromParcelLinks'][0].get('lrUnitId')
                post_data = {"parcelId": int(parcel_id), "lrUnitId": lr_unit_id_from_parcel}
            else:
                post_data = {"parcelIds": [int(parcel_id)]}
            
            print(f"✓ Fallback POST data: {post_data}")
            lr_response = requests.post(lr_units_url, headers=headers, cookies=cookie_dict, json=post_data)
            
            if lr_response.status_code == 200:
                lr_data = lr_response.json()
                print(f"✓ Fallback response received (Status: {lr_response.status_code})")
                
                if lr_data and len(lr_data) > 0 and 'lrUnitId' in lr_data[0]:
                    lr_unit_id = lr_data[0]['lrUnitId']
                    print(f"✓ Retrieved lrUnitId: {lr_unit_id} from fallback request")
                else:
                    print("⚠ No lrUnitId found in fallback response")
            else:
                print(f"✗ Fallback request failed (Status: {lr_response.status_code})")
                print(f"✗ Fallback response: {lr_response.text}")
            
        except Exception as e:
            print(f"⚠ Warning: Error with fallback land registry request: {e}")
    
    # Download LDB extract if we have lr_unit_id
    if lr_unit_id:
        await download_ldb_extract_pdf(lr_unit_id, headers, cookie_dict, search_text, search_dir)
    else:
        print("⚠ No land registry unit ID found - cannot download land registry extract")


async def download_ldb_extract_pdf(lr_unit_id, headers, cookie_dict, search_text, search_dir):
    """
    Download LDB extract PDF
    
    Flow:
    1. Use lrUnitId to request: https://oss.uredjenazemlja.hr/oss/public/lr-units/for-ldb-extract?lrUnitId={lrUnitId}&historical=0
    2. Extract fileUrl from response
    3. Download PDF from: https://oss.uredjenazemlja.hr/oss/public/reports/ldb-extract/{fileUrl}
    """
    ldb_extract_url = f"https://oss.uredjenazemlja.hr/oss/public/lr-units/for-ldb-extract?lrUnitId={lr_unit_id}&historical=0"
    
    try:
        print(f"Downloading land registry database extract for lrUnitId: {lr_unit_id}")
        ldb_response = requests.get(ldb_extract_url, headers=headers, cookies=cookie_dict)
        
        if ldb_response.status_code == 200:
            try:
                ldb_data = ldb_response.json()
                print(f"✓ LDB extract response received (Status: {ldb_response.status_code})")
                
                if ldb_data and 'fileUrl' in ldb_data:
                    file_url = ldb_data['fileUrl']
                    
                    # Construct the full PDF URL
                    full_pdf_url = f"https://oss.uredjenazemlja.hr/oss/public/reports/ldb-extract/{file_url}"
                    
                    print("Downloading land registry PDF...")
                    ldb_pdf_response = requests.get(full_pdf_url, headers=headers, cookies=cookie_dict)
                    
                    if ldb_pdf_response.status_code == 200:
                        filename = file_url.split('/')[-1] if '/' in file_url else file_url
                        save_path = os.path.join(search_dir, f"LandRegistry_{filename}")
                        
                        with open(save_path, 'wb') as f:
                            f.write(ldb_pdf_response.content)
                        print(f"✓ Downloaded: {save_path}")
                        print(f"✓ File size: {len(ldb_pdf_response.content)} bytes")
                    else:
                        print(f"✗ Failed to download land registry PDF (Status: {ldb_pdf_response.status_code})")
                else:
                    print("⚠ No fileUrl found in LDB extract response")
                    
            except Exception as e:
                print(f"✗ Error processing LDB extract response: {e}")
        else:
            print(f"✗ LDB extract API request failed (Status: {ldb_response.status_code})")
            print(f"✗ LDB extract response: {ldb_response.text}")
            
    except Exception as e:
        print(f"✗ Error downloading land registry PDF: {e}")


if __name__ == "__main__":
    # Ask user for search address
    search_text = input("Enter the address (e.g. 'Ilica 55'): ").strip()

    # Headless mode
    headless = True

    asyncio.run(run_browser(search_text, headless))
