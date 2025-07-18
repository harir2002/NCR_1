
# -*- coding: utf-8 -*-
import io
import streamlit as st
import requests
import json
import urllib.parse
import urllib3
import certifi
import pandas as pd  
from bs4 import BeautifulSoup
from datetime import datetime
import re
import logging
import os
from dotenv import load_dotenv
from io import BytesIO
import base64
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Tuple, Dict, Any

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# WatsonX configuration
WATSONX_API_URL = os.getenv("WATSONX_API_URL")
MODEL_ID = os.getenv("MODEL_ID")
PROJECT_ID = os.getenv("PROJECT_ID")
API_KEY = os.getenv("API_KEY")

# Check environment variables
if not all([API_KEY, WATSONX_API_URL, MODEL_ID, PROJECT_ID]):
    st.error("❌ Missing environment variables. Please set API_KEY, WATSONX_API_URL, MODEL_ID, and PROJECT_ID in your .env file.")
    st.markdown("**Setup Instructions**:\n1. Create a `.env` file with the following:\n```\nAPI_KEY=your_api_key\nWATSONX_API_URL=your_url\nMODEL_ID=your_model_id\nPROJECT_ID=your_project_id\n```\n2. Restart the application.")
    logger.error("Missing one or more required environment variables")
    st.stop()

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# API Endpoints
LOGIN_URL = "https://dms.asite.com/apilogin/"
SEARCH_URL = "https://adoddleak.asite.com/commonapi/formsearchapi/search"
IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"

# Function to generate access token
def get_access_token(API_KEY):
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    data = {"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": API_KEY}
    try:
        response = requests.post(IAM_TOKEN_URL, headers=headers, data=data, verify=certifi.where(), timeout=50)
        if response.status_code == 200:
            token_info = response.json()
            logger.info("Access token generated successfully")
            return token_info['access_token']
        else:
            logger.error(f"Failed to get access token: {response.status_code} - {response.text}")
            st.error(f"❌ Failed to get access token: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Exception getting access token: {str(e)}")
        st.error(f"❌ Error getting access token: {str(e)}")
        return None

# Login Function
def login_to_asite(email, password):
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    payload = {"emailId": email, "password": password}
    response = requests.post(LOGIN_URL, headers=headers, data=payload, verify=certifi.where(), timeout=50)
    if response.status_code == 200:
        try:
            session_id = response.json().get("UserProfile", {}).get("Sessionid")
            logger.info(f"Login successful, Session ID: {session_id}")
            return session_id
        except json.JSONDecodeError:
            logger.error("JSONDecodeError during login")
            st.error("❌ Failed to parse login response")
            return None
    logger.error(f"Login failed: {response.status_code}")
    st.error(f"❌ Login failed: {response.status_code}")
    return None

# Fetch Data Function
def fetch_project_data(session_id, project_name, form_name, record_limit=1000):
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "Cookie": f"ASessionID={session_id}"}
    all_data = []
    start_record = 1
    total_records = None

     # Capture start time
    start_time = datetime.now()
    st.write(f"🔄 Fetching data from Asite started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Started fetching data from Asite for project '{project_name}', form '{form_name}' at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")


    with st.spinner("Fetching data from Asite..."):
        while True:
            search_criteria = {"criteria": [{"field": "ProjectName", "operator": 1, "values": [project_name]}, {"field": "FormName", "operator": 1, "values": [form_name]}], "recordStart": start_record, "recordLimit": record_limit}
            search_criteria_str = json.dumps(search_criteria)
            encoded_payload = f"searchCriteria={urllib.parse.quote(search_criteria_str)}"
            response = requests.post(SEARCH_URL, headers=headers, data=encoded_payload, verify=certifi.where(), timeout=50)

            try:
                response_json = response.json()
                if total_records is None:
                    total_records = response_json.get("responseHeader", {}).get("results-total", 0)
                all_data.extend(response_json.get("FormList", {}).get("Form", []))
                st.info(f"🔄 Fetched {len(all_data)} / {total_records} records")
                if start_record + record_limit - 1 >= total_records:
                    break
                start_record += record_limit
            except Exception as e:
                logger.error(f"Error fetching data: {str(e)}")
                st.error(f"❌ Error fetching data: {str(e)}")
                break

    # Capture end time
    end_time = datetime.now()
    st.write(f"🔄 Fetching data from Asite completed at {end_time.strftime('%Y-%m-%d %H:%M:%S')} (Duration: {(end_time - start_time).total_seconds()} seconds)")
    logger.info(f"Finished fetching data from Asite for project '{project_name}', form '{form_name}' at {end_time.strftime('%Y-%m-%d %H:%M:%S')} (Duration: {(end_time - start_time).total_seconds()} seconds)")

    return {"responseHeader": {"results": len(all_data), "total_results": total_records}}, all_data, encoded_payload

# Process JSON Data
def process_json_data(json_data):
    data = []
    for item in json_data:
        form_details = item.get('FormDetails', {})
        created_date = form_details.get('FormCreationDate', None)
        expected_close_date = form_details.get('UpdateDate', None)
        form_status = form_details.get('FormStatus', None)
        
        discipline = None
        description = None
        custom_fields = form_details.get('CustomFields', {}).get('CustomField', [])
        for field in custom_fields:
            if field.get('FieldName') == 'CFID_DD_DISC':
                discipline = field.get('FieldValue', None)
            elif field.get('FieldName') == 'CFID_RTA_DES':
                description = BeautifulSoup(field.get('FieldValue', None) or '', "html.parser").get_text()

        days_diff = None
        if created_date and expected_close_date:
            try:
                created_date_obj = datetime.strptime(created_date.split('#')[0], "%d-%b-%Y")
                expected_close_date_obj = datetime.strptime(expected_close_date.split('#')[0], "%d-%b-%Y")
                days_diff = (expected_close_date_obj - created_date_obj).days
            except Exception as e:
                logger.error(f"Error calculating days difference: {str(e)}")
                days_diff = None

        data.append([days_diff, created_date, expected_close_date, description, form_status, discipline])

    df = pd.DataFrame(data, columns=['Days', 'Created Date (WET)', 'Expected Close Date (WET)', 'Description', 'Status', 'Discipline'])
    df['Created Date (WET)'] = pd.to_datetime(df['Created Date (WET)'].str.split('#').str[0], format="%d-%b-%Y", errors='coerce')
    df['Expected Close Date (WET)'] = pd.to_datetime(df['Expected Close Date (WET)'].str.split('#').str[0], format="%d-%b-%Y", errors='coerce')
    logger.debug(f"DataFrame columns after processing: {df.columns.tolist()}")
    if df.empty:
        logger.warning("DataFrame is empty after processing")
        st.warning("⚠️ No data processed. Check the API response.")
    return df

# Clean and Parse JSON
def clean_and_parse_json(text):
    import re
    import json
    
    json_match = re.search(r'(\{.*\})', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    start_idx = text.find('{')
    end_idx = text.rfind('}')
    
    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        try:
            json_str = text[start_idx:end_idx+1]
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON even after extraction: {json_str}")
            
    logger.error(f"Could not extract valid JSON from: {text}")
    return None

# Generate NCR Report


@st.cache_data
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type((requests.RequestException, ValueError, KeyError)))
def generate_ncr_report_for_eden(df: pd.DataFrame, report_type: str, start_date=None, end_date=None, until_date=None) -> Tuple[Dict[str, Any], str]:
    try:
        with st.spinner(f"Generating {report_type} NCR Report..."):
            # Input validation
            if df is None or df.empty:
                error_msg = "❌ Input DataFrame is empty or None"
                st.error(error_msg)
                return {"error": "Empty DataFrame"}, ""
            
            if report_type not in ["Open", "Closed"]:
                error_msg = f"❌ Invalid report_type: {report_type}. Must be 'Open' or 'Closed'"
                st.error(error_msg)
                return {"error": "Invalid report_type"}, ""
            
            # Ensure the DataFrame has no NaT values in critical columns for filtering
            df = df.copy()
            
            # Check if required columns exist
            required_columns = ['Created Date (WET)', 'Status']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                error_msg = f"❌ Missing required columns: {missing_columns}"
                st.error(error_msg)
                return {"error": f"Missing columns: {missing_columns}"}, ""
            
            # Clean the DataFrame
            df = df[df['Created Date (WET)'].notna()]
            
            if report_type == "Closed":
                try:
                    # Ensure Expected Close Date column exists for Closed reports
                    if 'Expected Close Date (WET)' not in df.columns:
                        error_msg = "❌ 'Expected Close Date (WET)' column is required for Closed reports"
                        st.error(error_msg)
                        return {"error": "Missing Expected Close Date column"}, ""
                    
                    start_date = pd.to_datetime(start_date) if start_date else df['Created Date (WET)'].min()
                    end_date = pd.to_datetime(end_date) if end_date else df['Expected Close Date (WET)'].max()
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid date range: {str(e)}")
                    st.error(f"❌ Invalid date range: {str(e)}")
                    return {"error": "Invalid date range"}, ""

                df = df[df['Expected Close Date (WET)'].notna()]
                
                # Ensure 'Days' column exists or calculate it
                if 'Days' not in df.columns:
                    try:
                        df['Days'] = (pd.to_datetime(df['Expected Close Date (WET)']) - pd.to_datetime(df['Created Date (WET)'])).dt.days
                    except Exception as e:
                        logger.error(f"Error calculating Days column: {str(e)}")
                        st.error(f"❌ Error calculating Days: {str(e)}")
                        return {"error": "Error calculating Days"}, ""
                
                filtered_df = df[
                    (df['Status'] == 'Closed') &
                    (pd.to_datetime(df['Created Date (WET)']) >= start_date) &
                    (pd.to_datetime(df['Created Date (WET)']) <= end_date) &
                    (pd.to_numeric(df['Days'], errors='coerce') > 21)
                ].copy()
                
            else:  # Open report
                if until_date is None:  # Changed from Until_Date to until_date
                    logger.error("Open Until Date is required for Open NCR Report")
                    st.error("❌ Open Until Date is required for Open NCR Report")
                    return {"error": "Open Until Date is required"}, ""
                
                try:
                    today = pd.to_datetime(until_date)  # Changed from Until_Date to until_date
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid Open Until Date: {str(e)}")
                    st.error(f"❌ Invalid Open Until Date: {str(e)}")
                    return {"error": "Invalid Open Until Date"}, ""
                    
                filtered_df = df[
                    (df['Status'] == 'Open') &
                    (df['Created Date (WET)'].notna())
                ].copy()
                
                try:
                    filtered_df.loc[:, 'Days_From_Today'] = (today - pd.to_datetime(filtered_df['Created Date (WET)'])).dt.days
                    filtered_df = filtered_df[filtered_df['Days_From_Today'] > 21].copy()
                except Exception as e:
                    logger.error(f"Error calculating Days_From_Today: {str(e)}")
                    st.error(f"❌ Error calculating Days_From_Today: {str(e)}")
                    return {"error": "Error calculating Days_From_Today"}, ""

            if filtered_df.empty:
                st.warning(f"No {report_type} NCRs found with duration > 21 days. Try adjusting the date range or criteria.")
                return {"error": f"No {report_type} records found with duration > 21 days"}, ""

            # Safe string conversion
            try:
                filtered_df.loc[:, 'Created Date (WET)'] = filtered_df['Created Date (WET)'].astype(str)
                if 'Expected Close Date (WET)' in filtered_df.columns:
                    filtered_df.loc[:, 'Expected Close Date (WET)'] = filtered_df['Expected Close Date (WET)'].astype(str)
            except Exception as e:
                logger.error(f"Error converting dates to string: {str(e)}")
                st.error(f"❌ Error converting dates: {str(e)}")
                return {"error": "Error converting dates"}, ""

            processed_data = filtered_df.to_dict(orient="records")
            
            cleaned_data = []
            unique_records = []

            # Pre-compile regex patterns for better performance
            common_pattern = re.compile(r"common area|flat\s*no", re.IGNORECASE)

            for record in processed_data:
                try:
                    cleaned_record = {
                        "Description": str(record.get("Description", "")),
                        "Discipline": str(record.get("Discipline", "")),
                        "Created Date (WET)": str(record.get("Created Date (WET)", "")),
                        "Expected Close Date (WET)": str(record.get("Expected Close Date (WET)", "")),
                        "Status": str(record.get("Status", "")),
                        "Days": int(record.get("Days", 0)) if pd.notna(record.get("Days")) else 0,
                        "Tower": "External Development"
                    }
                    
                    if report_type == "Open":
                        cleaned_record["Days_From_Today"] = int(record.get("Days_From_Today", 0)) if pd.notna(record.get("Days_From_Today")) else 0

                    description = cleaned_record["Description"].lower()

                    if not description:
                        continue 
                    
                    # FIXED POUR EXTRACTION LOGIC
                    if common_pattern.search(description):
                        cleaned_record["Pours"] = ["Common"]
                    else:
                        pours = set()
                        
                        # Pattern 1: Handle ranges like "1 to 8", "1-8", "5 to 10"
                        range_pattern = r"(?:pour|p)[-\s]*(\d+)[-\s]*(?:to|-)[-\s]*(\d+)"
                        range_matches = re.findall(range_pattern, description, re.IGNORECASE)
                        
                        for start_str, end_str in range_matches:
                            try:
                                start_num = int(start_str)
                                end_num = int(end_str)
                                if start_num <= end_num and end_num - start_num <= 20:  # Reasonable limit
                                    pours.update(f"P{i}" for i in range(start_num, end_num + 1))
                            except ValueError:
                                continue
                        
                        # Pattern 2: Handle comma/and separated lists like "1,2, 3 & 4", "1, 2 and 3"
                        if not pours:
                            list_pattern = r"(?:pour|p)[-\s]*([0-9,\s&and]+)"
                            list_matches = re.findall(list_pattern, description, re.IGNORECASE)
                            
                            for match in list_matches:
                                # Extract all individual numbers from the matched string
                                numbers = re.findall(r'\d+', match)
                                for num_str in numbers:
                                    try:
                                        num = int(num_str)
                                        if num <= 50:  # Reasonable pour number limit
                                            pours.add(f"P{num}")
                                    except ValueError:
                                        continue
                        
                        # Pattern 3: Handle individual pour references like "P1", "pour 5"
                        if not pours:
                            # More specific pattern to avoid matching numbers that are part of other contexts
                            individual_pattern = r"(?:pour|p)[-\s]*(\d+)(?!\s*(?:to|-)\s*\d+)"
                            individual_matches = re.findall(individual_pattern, description, re.IGNORECASE)
                            
                            for num_str in individual_matches:
                                try:
                                    num = int(num_str)
                                    if num <= 50:
                                        pours.add(f"P{num}")
                                except ValueError:
                                    continue
                        
                        cleaned_record["Pours"] = sorted(list(pours)) if pours else ["Common"]
                
                    # Initialize Discipline_Category
                    discipline = cleaned_record["Discipline"].strip().lower()
                    if discipline == "none" or not discipline:
                        logger.debug(f"Skipping record with invalid discipline: {discipline}")
                        continue
                    elif "hse" in discipline:
                        logger.debug(f"Skipping HSE record: {discipline}")
                        cleaned_record["Discipline_Category"] = "HSE"
                        continue  # Skip HSE records entirely
                    elif "structure" in discipline or "sw" in discipline:
                        cleaned_record["Discipline_Category"] = "SW"
                    elif "civil" in discipline or "finishing" in discipline or "fw" in discipline:
                        cleaned_record["Discipline_Category"] = "FW"
                    else:
                        cleaned_record["Discipline_Category"] = "MEP"
                        
                    unique_records.append(cleaned_record["Description"])
                        
                    unique_records.append(cleaned_record["Description"])
                    # Check for duplicates based on Description
                    # Tower categorization
                    if any(phrase in description for phrase in ["eden clubhouse", "eden-clubhouse", "eden club"]):
                        cleaned_record["Tower"] = "Eden-Club"
                        logger.debug(f"Matched 'Eden Clubhouse' in description: {description}")
                        cleaned_data.append(cleaned_record)
                    else:
                        tower_matches = re.findall(r"(tower|t)\s*-?\s*(\d+)", description, re.IGNORECASE)
                        multiple_tower_pattern = re.search(
                            r"(tower|t)\s*-?\s*(\d+)\s*([,&]|and)\s*(tower|t)?\s*-?\s*(\d+)",
                            description,
                            re.IGNORECASE
                        )
                        flat_no_pattern = re.search(r"flat\s*no", description, re.IGNORECASE)
                        
                        if multiple_tower_pattern:
                            # FIXED: Create a single record that represents both towers instead of duplicating
                            tower1 = multiple_tower_pattern.group(2).zfill(2)
                            tower2 = multiple_tower_pattern.group(5).zfill(2)
                            
                            # Option 1: Create a combined tower name
                            cleaned_record["Tower"] = f"Eden-Tower-{tower1}-{tower2}-CommonArea"
                            cleaned_data.append(cleaned_record)
                            logger.debug(f"Added combined tower record for Eden-Tower-{tower1}-{tower2}-CommonArea: {description}")
                            
                            
                            
                        elif flat_no_pattern and tower_matches:
                            tower_num = tower_matches[0][1].zfill(2)
                            cleaned_record["Tower"] = f"Eden-Tower-{tower_num}"
                            cleaned_data.append(cleaned_record)
                            logger.debug(f"Assigned Eden-Tower-{tower_num} for Flat no description: {description}")
                        elif "common area" in description or not tower_matches:
                            cleaned_record["Tower"] = "Common_Area"
                            cleaned_data.append(cleaned_record)
                            logger.debug(f"Assigned Common_Area: {description}")
                        else:
                            tower_num = tower_matches[0][1].zfill(2)
                            cleaned_record["Tower"] = f"Eden-Tower-{tower_num}"
                            logger.debug(f"Single tower match: Eden-Tower-{tower_num}")
                            cleaned_data.append(cleaned_record)
                            
                except Exception as e:
                    logger.error(f"Error processing record: {record}, error: {str(e)}")
                    continue
            
            # Remove duplicates
            try:
                cleaned_data = [dict(t) for t in {tuple(sorted(d.items())) for d in cleaned_data}]
            except Exception as e:
                logger.error(f"Error removing duplicates: {str(e)}")
                # If deduplication fails, continue with original data
                pass

            if not cleaned_data:
                return {report_type: {"Sites": {}, "Grand_Total": 0}}, ""

            # Get access token
            try:
                access_token = get_access_token(API_KEY)
                if not access_token:
                    return {"error": "Failed to obtain access token"}, ""
            except Exception as e:
                logger.error(f"Error getting access token: {str(e)}")
                return {"error": f"Failed to obtain access token: {str(e)}"}, ""

            all_results = {report_type: {"Sites": {}, "Grand_Total": 0}}
            chunk_size = int(os.getenv("CHUNK_SIZE", 20))
            
            for i in range(0, len(cleaned_data), chunk_size):
                chunk = cleaned_data[i:i + chunk_size]
                
                # Capture and log start time
                start_time = datetime.now()
                st.write(f" 🔄 Processing chunk {i // chunk_size + 1}: Records {i} to {min(i + chunk_size, len(cleaned_data))} started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"Started chunk {i // chunk_size + 1} at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # Log data sent to WatsonX
                logger.info(f"Data sent to WatsonX for {report_type} chunk {i // chunk_size + 1}: {json.dumps(chunk, indent=2)}")

                # Log the total number of records being processed
                total_records = len(cleaned_data)
                st.write(f"Total {report_type} records to process: {total_records}")
                logger.info(f"Total {report_type} records to process: {total_records}")

                prompt = (
                    "IMPORTANT: RETURN ONLY A SINGLE VALID JSON OBJECT WITH THE EXACT FIELDS SPECIFIED BELOW. "
                    "DO NOT GENERATE ANY CODE (e.g., Python, JavaScript). "
                    "DO NOT INCLUDE ANY TEXT, EXPLANATIONS, OR MULTIPLE RESPONSES OUTSIDE THE JSON OBJECT. "
                    "DO NOT WRAP THE JSON IN CODE BLOCKS (e.g., ```json). "
                    "RETURN THE JSON OBJECT DIRECTLY.\n\n"
                    f"Task: Group the provided data by 'Tower' and collect 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status', 'Discipline', and 'Pours' into arrays. "
                    f"Count the records by 'Discipline_Category' ('SW', 'FW', 'MEP'), calculate the 'Total' for each 'Tower', and count occurrences of each pour within 'Pours' (e.g., P1, P2). "
                    f"Process ALL {len(chunk)} records provided in the data.\n"
                    f"Use 'Tower' values (e.g., 'Eden-Tower-04-CommonArea', 'Eden-Tower-07-CommonArea', 'Common_Area'), "
                    f"'Discipline_Category' values (e.g., 'SW', 'FW', 'MEP'), and provided 'Pours' values. Count each record exactly once.\n\n"
                    "REQUIRED OUTPUT FORMAT (ONLY THESE FIELDS):\n"
                    "{\n"
                    f'  "{report_type}": {{\n'
                    '    "Sites": {\n'
                    '      "Site_Name1": {\n'
                    '        "Descriptions": ["description1", "description2"],\n'
                    '        "Created Date (WET)": ["date1", "date2"],\n'
                    '        "Expected Close Date (WET)": ["date1", "date2"],\n'
                    '        "Status": ["status1", "status2"],\n'
                    '        "Discipline": ["discipline1", "discipline2"],\n'
                    '        "Pours": [["pour1a", "pour1b"], ["pour2"]],\n'
                    '        "SW": number,\n'
                    '        "FW": number,\n'
                    '        "MEP": number,\n'
                    '        "Total": number,\n'
                    '        "PoursCount": {"pour1": count1, "pour2": count2}\n'
                    '      }\n'
                    '    },\n'
                    f'    "Grand_Total": {len(chunk)}\n'
                    '  }\n'
                    '}\n\n'
                    f"Data: {json.dumps(chunk)}\n"
                    f"IMPORTANT: Ensure the JSON is valid and contains all required fields. "    
                    f"Return the result strictly as a JSON object—no code, no explanations, only the JSON.Dont put <|eom_id|> or any other markers in the JSON output. Grand_Total must be {len(chunk)}."
                )

                payload = {
                    "input": prompt,
                    "parameters": {
                        "decoding_method": "greedy",
                        "max_new_tokens": 5100,
                        "min_new_tokens": 0,
                        "temperature": 0.0
                    },
                    "model_id": MODEL_ID,
                    "project_id": PROJECT_ID
                }
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {access_token}"
                }

                retry_strategy = Retry(
                    total=3,
                    backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["POST"]
                )
                adapter = HTTPAdapter(max_retries=retry_strategy)
                http = requests.Session()
                http.mount("https://", adapter)

                try:
                    start_time = datetime.now()
                    response = http.post(WATSONX_API_URL, headers=headers, json=payload, verify=certifi.where(), timeout=1000)
                    logger.info(f"WatsonX API call took {(datetime.now() - start_time).total_seconds()} seconds for {len(chunk)} records")
                    st.write(f"Debug - Response status code: {response.status_code}")

                    if response.status_code == 200:
                        api_result = response.json()
                        generated_text = api_result.get("results", [{}])[0].get("generated_text", "").strip()
                        short_text = generated_text[:200] + "..." if len(generated_text) > 200 else generated_text
                        logger.debug(f"Parsed generated text: {generated_text}")

                        parsed_json = clean_and_parse_json(generated_text)
                        if parsed_json and report_type in parsed_json:
                            chunk_result = parsed_json[report_type]
                            st.write(f"Processing API response for chunk {i // chunk_size + 1} with {len(chunk)} records")
                            
                            for site, data in chunk_result["Sites"].items():
                                if site not in all_results[report_type]["Sites"]:
                                    all_results[report_type]["Sites"][site] = {
                                        "Descriptions": [],
                                        "Created Date (WET)": [],
                                        "Expected Close Date (WET)": [],
                                        "Status": [],
                                        "Discipline": [],
                                        "Pours": [],
                                        "SW": 0,
                                        "FW": 0,
                                        "MEP": 0,
                                        "Total": 0,
                                        "PoursCount": {}
                                    }
                                all_results[report_type]["Sites"][site]["Descriptions"].extend(data["Descriptions"])
                                all_results[report_type]["Sites"][site]["Created Date (WET)"].extend(data["Created Date (WET)"])
                                all_results[report_type]["Sites"][site]["Expected Close Date (WET)"].extend(data["Expected Close Date (WET)"])
                                all_results[report_type]["Sites"][site]["Status"].extend(data["Status"])
                                all_results[report_type]["Sites"][site]["Discipline"].extend(data["Discipline"])
                                all_results[report_type]["Sites"][site]["Pours"].extend(data["Pours"])
                                all_results[report_type]["Sites"][site]["SW"] += data["SW"]
                                all_results[report_type]["Sites"][site]["FW"] += data["FW"]
                                all_results[report_type]["Sites"][site]["MEP"] += data["MEP"]
                                all_results[report_type]["Sites"][site]["Total"] += data["Total"]
                                for pour, count in data["PoursCount"].items():
                                    all_results[report_type]["Sites"][site]["PoursCount"][pour] = all_results[report_type]["Sites"][site]["PoursCount"].get(pour, 0) + count
                            
                            # Use the actual number of records processed instead of API count
                            all_results[report_type]["Grand_Total"] += len(chunk)
                            st.write(f"Successfully processed chunk {i // chunk_size + 1} with {len(chunk)} records")
                        else:
                            logger.error("No valid JSON found in response")
                            st.write("Falling back to local count for this chunk")
                            process_chunk_locally(chunk, all_results, report_type)
                    else:
                        error_msg = f"❌ WatsonX API error: {response.status_code} - {response.text}"
                        st.error(error_msg)
                        logger.error(error_msg)
                        st.write("Falling back to local count for this chunk")
                        process_chunk_locally(chunk, all_results, report_type)
                        
                except requests.RequestException as e:
                    error_msg = f"❌ Request exception during WatsonX call: {str(e)}"
                    st.error(error_msg)
                    logger.error(error_msg)
                    st.write("Falling back to local count for this chunk")
                    process_chunk_locally(chunk, all_results, report_type)
                except Exception as e:
                    error_msg = f"❌ Exception during WatsonX call: {str(e)}"
                    st.error(error_msg)
                    logger.error(error_msg)
                    st.write("Falling back to local count for this chunk")
                    process_chunk_locally(chunk, all_results, report_type)

                end_time = datetime.now()
                st.write(f"🔄 Chunk {i // chunk_size + 1} model processing for {report_type} completed at {end_time.strftime('%Y-%m-%d %H:%M:%S')} (Duration: {(end_time - start_time).total_seconds()} seconds)")
                logger.info(f"Finished model processing chunk {i // chunk_size + 1} for {report_type} at {end_time.strftime('%Y-%m-%d %H:%M:%S')} (Duration: {(end_time - start_time).total_seconds()} seconds)")

            table_data = []
            for site, data in all_results[report_type]["Sites"].items():
                row = {
                    "Site": site,
                    "SW Count": data["SW"],
                    "FW Count": data["FW"],
                    "MEP Count": data["MEP"],
                    "Total Records": data["Total"],
                    "Pours Count": json.dumps(data["PoursCount"], indent=2),
                    "Descriptions": "; ".join(data["Descriptions"]),
                    "Created Dates": "; ".join(data["Created Date (WET)"]),
                    "Expected Close Dates": "; ".join(data["Expected Close Date (WET)"]),
                    "Statuses": "; ".join(data["Status"]),
                    "Disciplines": "; ".join(data["Discipline"]),
                    "Pours": "; ".join([", ".join(m) for m in data["Pours"]])
                }
                table_data.append(row)
            
            if table_data:
                df_table = pd.DataFrame(table_data)
                st.write(f"Final {report_type} Results:")
                st.dataframe(df_table, use_container_width=True)
            else:
                st.write(f"No data available for {report_type} report.")

            return all_results, ""  # Ensure return even after processing all chunks

    except TypeError as e:
        logger.error(f"TypeError in generate_ncr_report: {str(e)}")
        st.error(f"❌ Type Error: {str(e)}")
        return {"error": f"Type Error: {str(e)}"}, ""
    except Exception as e:
        logger.error(f"Unexpected error in generate_ncr_report: {str(e)}")
        st.error(f"❌ Unexpected Error: {str(e)}")
        return {"error": f"Unexpected Error: {str(e)}"}, ""

@st.cache_data
def process_chunk_locally(chunk, all_results, report_type):
    """Helper function to process chunks locally when API fails"""
    try:
        for record in chunk:
            tower = record.get("Tower", "Unknown")
            discipline = record.get("Discipline_Category", "Unknown")
            pours = record.get("Pours", ["Common"])
            
            if tower not in all_results[report_type]["Sites"]:
                all_results[report_type]["Sites"][tower] = {
                    "Descriptions": [],
                    "Created Date (WET)": [],
                    "Expected Close Date (WET)": [],
                    "Status": [],
                    "Discipline": [],
                    "Pours": [],
                    "SW": 0,
                    "FW": 0,
                    "MEP": 0,
                    "Total": 0,
                    "PoursCount": {}
                }
            
            all_results[report_type]["Sites"][tower]["Descriptions"].append(record.get("Description", ""))
            all_results[report_type]["Sites"][tower]["Created Date (WET)"].append(record.get("Created Date (WET)", ""))
            all_results[report_type]["Sites"][tower]["Expected Close Date (WET)"].append(record.get("Expected Close Date (WET)", ""))
            all_results[report_type]["Sites"][tower]["Status"].append(record.get("Status", ""))
            all_results[report_type]["Sites"][tower]["Discipline"].append(record.get("Discipline", ""))
            all_results[report_type]["Sites"][tower]["Pours"].append(pours)
            
            if discipline in ["SW", "FW", "MEP"]:
                all_results[report_type]["Sites"][tower][discipline] += 1
            
            all_results[report_type]["Sites"][tower]["Total"] += 1
            
            for pour in pours:
                all_results[report_type]["Sites"][tower]["PoursCount"][pour] = all_results[report_type]["Sites"][tower]["PoursCount"].get(pour, 0) + 1
                
            all_results[report_type]["Grand_Total"] += 1
            
    except Exception as e:
        logger.error(f"Error in local processing: {str(e)}")
        st.error(f"❌ Error in local processing: {str(e)}")

# Generate NCR Housekeeping Report

@st.cache_data

def generate_ncr_Housekeeping_report_for_eden(df, report_type, start_date=None, end_date=None, until_date=None):
    """Generate Housekeeping NCR report for Open or Closed records."""
    with st.spinner(f"Generating {report_type} Housekeeping NCR Report with WatsonX..."):
        try:
            today = pd.to_datetime(datetime.today().strftime('%Y/%m/%d'))
            closed_start = pd.to_datetime(start_date) if start_date else None
            closed_end = pd.to_datetime(end_date) if end_date else None
            open_until = pd.to_datetime(until_date) if until_date else None

            # Define housekeeping keywords
            housekeeping_keywords = [
                'housekeeping', 'cleaning', 'cleanliness', 'waste disposal', 'waste management', 'garbage', 'trash',
                'rubbish', 'debris', 'litter', 'dust', 'untidy', 'cluttered', 'accumulation of waste',
                'construction waste', 'pile of garbage', 'poor housekeeping', 'material storage',
                'construction debris', 'cleaning schedule', 'garbage collection', 'waste bins', 'dirty',
                'mess', 'unclean', 'disorderly', 'dirty floor', 'waste disposal area', 'waste collection',
                'cleaning protocol', 'sanitation', 'trash removal', 'waste accumulation', 'unkept area',
                'refuse collection', 'workplace cleanliness'
            ]

            def is_housekeeping_record(description):
                # Handle None or non-string descriptions
                if description is None or not isinstance(description, str):
                    logger.debug(f"Invalid description encountered: {description}")
                    return False
                description_lower = description.lower()
                return any(keyword in description_lower for keyword in housekeeping_keywords)

            # Filter data
            if report_type == "Closed":
                filtered_df = df[
                    (df['Discipline'] == 'HSE') &
                    (df['Status'] == 'Closed') &
                    (df['Days'].notnull()) &
                    (df['Days'] > 7) &
                    (df['Description'].apply(is_housekeeping_record))
                ].copy()
                if closed_start and closed_end:
                    filtered_df = filtered_df[
                        (pd.to_datetime(filtered_df['Created Date (WET)']) >= closed_start) &
                        (pd.to_datetime(filtered_df['Expected Close Date (WET)']) <= closed_end)
                    ].copy()
            else:  # Open
                filtered_df = df[
                    (df['Discipline'] == 'HSE') &
                    (df['Status'] == 'Open') &
                    (pd.to_datetime(df['Created Date (WET)']).notna()) &
                    (df['Description'].apply(is_housekeeping_record))
                ].copy()
                filtered_df.loc[:, 'Days_From_Today'] = (today - pd.to_datetime(filtered_df['Created Date (WET)'])).dt.days
                filtered_df = filtered_df[filtered_df['Days_From_Today'] > 7].copy()
                if open_until:
                    filtered_df = filtered_df[
                        (pd.to_datetime(filtered_df['Created Date (WET)']) <= open_until)
                    ].copy()

            if filtered_df.empty:
                return {"Housekeeping": {"Sites": {}, "Grand_Total": 0}}, ""

            filtered_df.loc[:, 'Created Date (WET)'] = filtered_df['Created Date (WET)'].astype(str)
            filtered_df.loc[:, 'Expected Close Date (WET)'] = filtered_df['Expected Close Date (WET)'].astype(str)

            processed_data = filtered_df.to_dict(orient="records")
            
            cleaned_data = []
            seen_descriptions = set()
            for record in processed_data:
                description = str(record.get("Description", "")).strip()
                if description and description not in seen_descriptions:
                    seen_descriptions.add(description)
                    cleaned_record = {
                        "Description": description,
                        "Created Date (WET)": str(record.get("Created Date (WET)", "")),
                        "Expected Close Date (WET)": str(record.get("Expected Close Date (WET)", "")),
                        "Status": str(record.get("Status", "")),
                        "Days": record.get("Days", 0),
                        "Discipline": "HSE",
                        "Tower": "External Development"
                    }
                    if report_type == "Open":
                        cleaned_record["Days_From_Today"] = record.get("Days_From_Today", 0)

                    desc_lower = description.lower()
                    tower_match = re.search(r"(tower|t)\s*-?\s*(\d+|2021|28)", desc_lower, re.IGNORECASE)
                    cleaned_record["Tower"] = f"Eden-Tower{tower_match.group(2).zfill(2)}" if tower_match else "Common_Area"
                    logger.debug(f"Tower set to {cleaned_record['Tower']}")

                    cleaned_data.append(cleaned_record)

            st.write(f"Total {report_type} records to process: {len(cleaned_data)}")
            logger.debug(f"Processed data: {json.dumps(cleaned_data, indent=2)}")

            if not cleaned_data:
                return {"Housekeeping": {"Sites": {}, "Grand_Total": 0}}, ""

            access_token = get_access_token(API_KEY)
            if not access_token:
                return {"error": "Failed to obtain access token"}, ""

            result = {"Housekeeping": {"Sites": {}, "Grand_Total": 0}}
            chunk_size = 10
            total_chunks = (len(cleaned_data) + chunk_size - 1) // chunk_size

            session = requests.Session()
            retry_strategy = Retry(
                total=3,
                backoff_factor=2,
                status_forcelist=[500, 502, 503, 504, 429, 408],
                allowed_methods=["POST"],
                raise_on_redirect=True,
                raise_on_status=True
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)

            progress_placeholder = st.empty()
            status_placeholder = st.empty()
            error_placeholder = st.empty()
            progress_bar = progress_placeholder.progress(0)

            for i in range(0, len(cleaned_data), chunk_size):
                chunk = cleaned_data[i:i + chunk_size]
                current_chunk = i // chunk_size + 1
                progress = min((current_chunk / total_chunks) * 100, 100)
                progress_bar.progress(int(progress))
                status_placeholder.write(f"Processed {current_chunk}/{total_chunks} chunks ({int(progress)}%)")
                logger.debug(f"Chunk data: {json.dumps(chunk, indent=2)}")

                prompt = (
                    "Return a single valid JSON object with the exact fields specified below. Do not generate code, explanations, multiple responses, or wrap the JSON in code blocks. Process the provided data and return only the JSON object.\n\n"
                    "Task: Count Housekeeping NCRs by site ('Tower' field) where 'Discipline' is 'HSE' and 'Days' > 7 or 'Days_From_Today' > 7 for open records. The 'Description' must contain any of these housekeeping keywords (case-insensitive): "
                    "'housekeeping', 'cleaning', 'cleanliness', 'waste disposal', 'waste management', 'garbage', 'trash', 'rubbish', 'debris', 'litter', 'dust', 'untidy', 'cluttered', "
                    "'accumulation of waste', 'construction waste', 'pile of garbage', 'poor housekeeping', 'material storage', 'construction debris', 'cleaning schedule', 'garbage collection', "
                    "'waste bins', 'dirty', 'mess', 'unclean', 'disorderly', 'dirty floor', 'waste disposal area', 'waste collection', 'cleaning protocol', 'sanitation', 'trash removal', "
                    "'waste accumulation', 'unkept area', 'refuse collection', 'workplace cleanliness'. "
                    "Use 'Tower' values as they appear (e.g., 'Eden-Tower01', 'Common_Area'). Collect 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', and 'Status' into arrays for each site. "
                    "Assign the count to 'Count' (No. of Housekeeping NCRs beyond 7 days). If no matches, set count to 0 for each site in the data. Return all sites present in the data.\n\n"
                    "Output Format:\n"
                    "{\n"
                    '  "Housekeeping": {\n'
                    '    "Sites": {\n'
                    '      "Site_Name": {\n'
                    '        "Descriptions": [],\n'
                    '        "Created Date (WET)": [],\n'
                    '        "Expected Close Date (WET)": [],\n'
                    '        "Status": [],\n'
                    '        "Count": 0\n'
                    '      }\n'
                    '    },\n'
                    '    "Grand_Total": 0\n'
                    '  }\n'
                    '}\n\n'
                    f"Data: {json.dumps(chunk)}\n"
                )

                payload = {
                    "input": prompt,
                    "parameters": {
                        "decoding_method": "greedy",
                        "max_new_tokens": 500,
                        "min_new_tokens": 0,
                        "temperature": 0.001,
                        "n": 1
                    },
                    "model_id": MODEL_ID,
                    "project_id": PROJECT_ID
                }
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {access_token}"
                }

                try:
                    logger.debug("Initiating WatsonX API call...")
                    response = session.post(WATSONX_API_URL, headers=headers, json=payload, verify=certifi.where(), timeout=30)
                    logger.info(f"WatsonX API response status: {response.status_code}")

                    if response.status_code == 200:
                        api_result = response.json()
                        generated_text = api_result.get("results", [{}])[0].get("generated_text", "").strip()
                        logger.debug(f"Generated text for chunk {current_chunk}: {generated_text}")

                        json_str = None
                        brace_count = 0
                        start_idx = None
                        for idx, char in enumerate(generated_text):
                            if char == '{':
                                if brace_count == 0:
                                    start_idx = idx
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0 and start_idx is not None:
                                    json_str = generated_text[start_idx:idx + 1]
                                    break

                        if json_str:
                            try:
                                logger.debug(f"Extracted JSON string: {json_str}")
                                parsed_json = json.loads(json_str)
                                chunk_result = parsed_json.get("Housekeeping", {})
                                chunk_sites = chunk_result.get("Sites", {})
                                chunk_grand_total = chunk_result.get("Grand_Total", 0)

                                for site, values in chunk_sites.items():
                                    if not isinstance(values, dict):
                                        logger.warning(f"Invalid site data for {site}: {values}")
                                        continue
                                    if site not in result["Housekeeping"]["Sites"]:
                                        result["Housekeeping"]["Sites"][site] = {
                                            "Count": 0,
                                            "Descriptions": [],
                                            "Created Date (WET)": [],
                                            "Expected Close Date (WET)": [],
                                            "Status": []
                                        }
                                    result["Housekeeping"]["Sites"][site]["Descriptions"].extend(values.get("Descriptions", []))
                                    result["Housekeeping"]["Sites"][site]["Created Date (WET)"].extend(values.get("Created Date (WET)", []))
                                    result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"].extend(values.get("Expected Close Date (WET)", []))
                                    result["Housekeeping"]["Sites"][site]["Status"].extend(values.get("Status", []))
                                    result["Housekeeping"]["Sites"][site]["Count"] += values.get("Count", 0)
                                result["Housekeeping"]["Grand_Total"] += chunk_grand_total
                                logger.debug(f"Successfully processed chunk {current_chunk}/{total_chunks}")
                            except json.JSONDecodeError as e:
                                logger.error(f"JSONDecodeError for chunk {current_chunk}: {str(e)}")
                                error_placeholder.error(f"Failed to parse JSON for chunk {current_chunk}: {str(e)}")
                                for record in chunk:
                                    if is_housekeeping_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Today", 0) > 7) and record.get("Discipline") == "HSE":
                                        site = record["Tower"]
                                        if site not in result["Housekeeping"]["Sites"]:
                                            result["Housekeeping"]["Sites"][site] = {
                                                "Count": 0,
                                                "Descriptions": [],
                                                "Created Date (WET)": [],
                                                "Expected Close Date (WET)": [],
                                                "Status": []
                                            }
                                        result["Housekeeping"]["Sites"][site]["Descriptions"].append(record["Description"])
                                        result["Housekeeping"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                                        result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                                        result["Housekeeping"]["Sites"][site]["Status"].append(record["Status"])
                                        result["Housekeeping"]["Sites"][site]["Count"] += 1
                                        result["Housekeeping"]["Grand_Total"] += 1
                        else:
                            logger.error(f"No valid JSON for chunk {current_chunk}: {generated_text}")
                            error_placeholder.error(f"No valid JSON for chunk {current_chunk}")
                            for record in chunk:
                                if is_housekeeping_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Today", 0) > 7) and record.get("Discipline") == "HSE":
                                    site = record["Tower"]
                                    if site not in result["Housekeeping"]["Sites"]:
                                        result["Housekeeping"]["Sites"][site] = {
                                            "Count": 0,
                                            "Descriptions": [],
                                            "Created Date (WET)": [],
                                            "Expected Close Date (WET)": [],
                                            "Status": []
                                        }
                                    result["Housekeeping"]["Sites"][site]["Descriptions"].append(record["Description"])
                                    result["Housekeeping"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                                    result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                                    result["Housekeeping"]["Sites"][site]["Status"].append(record["Status"])
                                    result["Housekeeping"]["Sites"][site]["Count"] += 1
                                    result["Housekeeping"]["Grand_Total"] += 1
                    else:
                        logger.error(f"WatsonX API error for chunk {current_chunk}: {response.status_code} - {response.text}")
                        error_placeholder.error(f"WatsonX API error for chunk {current_chunk}: {response.status_code}")
                        for record in chunk:
                            if is_housekeeping_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Today", 0) > 7) and record.get("Discipline") == "HSE":
                                site = record["Tower"]
                                if site not in result["Housekeeping"]["Sites"]:
                                    result["Housekeeping"]["Sites"][site] = {
                                        "Count": 0,
                                        "Descriptions": [],
                                        "Created Date (WET)": [],
                                        "Expected Close Date (WET)": [],
                                        "Status": []
                                    }
                                result["Housekeeping"]["Sites"][site]["Descriptions"].append(record["Description"])
                                result["Housekeeping"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                                result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                                result["Housekeeping"]["Sites"][site]["Status"].append(record["Status"])
                                result["Housekeeping"]["Sites"][site]["Count"] += 1
                                result["Housekeeping"]["Grand_Total"] += 1
                except requests.exceptions.ReadTimeout as e:
                    logger.error(f"ReadTimeoutError for chunk {current_chunk}: {str(e)}")
                    error_placeholder.error(f"Failed to connect to WatsonX API for chunk {current_chunk}: {str(e)}")
                    for record in chunk:
                        if is_housekeeping_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Today", 0) > 7) and record.get("Discipline") == "HSE":
                            site = record["Tower"]
                            if site not in result["Housekeeping"]["Sites"]:
                                result["Housekeeping"]["Sites"][site] = {
                                    "Count": 0,
                                    "Descriptions": [],
                                    "Created Date (WET)": [],
                                    "Expected Close Date (WET)": [],
                                    "Status": []
                                }
                            result["Housekeeping"]["Sites"][site]["Descriptions"].append(record["Description"])
                            result["Housekeeping"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                            result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                            result["Housekeeping"]["Sites"][site]["Status"].append(record["Status"])
                            result["Housekeeping"]["Sites"][site]["Count"] += 1
                            result["Housekeeping"]["Grand_Total"] += 1
                except requests.exceptions.RequestException as e:
                    logger.error(f"RequestException for chunk {current_chunk}: {str(e)}")
                    error_placeholder.error(f"Failed to connect to WatsonX API for chunk {current_chunk}: {str(e)}")
                    for record in chunk:
                        if is_housekeeping_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Today", 0) > 7) and record.get("Discipline") == "HSE":
                            site = record["Tower"]
                            if site not in result["Housekeeping"]["Sites"]:
                                result["Housekeeping"]["Sites"][site] = {
                                    "Count": 0,
                                    "Descriptions": [],
                                    "Created Date (WET)": [],
                                    "Expected Close Date (WET)": [],
                                    "Status": []
                                }
                            result["Housekeeping"]["Sites"][site]["Descriptions"].append(record["Description"])
                            result["Housekeeping"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                            result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                            result["Housekeeping"]["Sites"][site]["Status"].append(record["Status"])
                            result["Housekeeping"]["Sites"][site]["Count"] += 1
                            result["Housekeeping"]["Grand_Total"] += 1

            progress_bar.progress(100)
            status_placeholder.write(f"Processed {total_chunks}/{total_chunks} chunks (100%)")
            logger.debug(f"Final result before deduplication: {json.dumps(result, indent=2)}")

            for site in result["Housekeeping"]["Sites"]:
                result["Housekeeping"]["Sites"][site]["Descriptions"] = list(set(result["Housekeeping"]["Sites"][site]["Descriptions"]))
                result["Housekeeping"]["Sites"][site]["Created Date (WET)"] = list(set(result["Housekeeping"]["Sites"][site]["Created Date (WET)"]))
                result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"] = list(set(result["Housekeeping"]["Sites"][site]["Expected Close Date (WET)"]))
                result["Housekeeping"]["Sites"][site]["Status"] = list(set(result["Housekeeping"]["Sites"][site]["Status"]))
            
            logger.debug(f"Final result after deduplication: {json.dumps(result, indent=2)}")
            return result, json.dumps(result)
        except Exception as e:
            logger.error(f"Unexpected error in generate_ncr_Housekeeping_report: {str(e)}")
            st.error(f"❌ Unexpected Error: {str(e)}")
            return {"error": f"Unexpected Error: {str(e)}"}, ""

def clean_and_parse_json(generated_text):
    # Remove code block markers if present
    cleaned_text = re.sub(r'```json|```python|```', '', generated_text).strip()
    
    # First attempt: Try to parse the text directly as JSON
    try:
        for line in cleaned_text.split('\n'):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                return json.loads(line)
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        logger.warning(f"Initial JSONDecodeError: {str(e)} - Cleaned response: {cleaned_text}")
    
    # Second attempt: If the response contains Python code with a print(json.dumps(...)),
    # extract the JSON from the output
    json_match = re.search(r'print$$ json\.dumps\((.*?),\s*indent=2 $$\)', cleaned_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
        try:
            return eval(json_str)  # Safely evaluate the JSON string as a Python dict
        except Exception as e:
            logger.error(f"Failed to evaluate extracted JSON: {str(e)} - Extracted JSON: {json_str}")
    
    logger.error(f"JSONDecodeError: Unable to parse response - Cleaned response: {cleaned_text}")
    return None

@st.cache_data
def generate_ncr_Safety_report_for_eden(df, report_type, start_date=None, end_date=None, until_date=None, debug_bypass_api=False):
    """Generate Safety NCR report for Open or Closed records."""
    with st.spinner(f"Generating {report_type} Safety NCR Report with WatsonX..."):
        try:
            today = pd.to_datetime(datetime.today().strftime('%Y/%m/%d'))
            closed_start = pd.to_datetime(start_date) if start_date else None
            closed_end = pd.to_datetime(end_date) if end_date else None
            open_until = pd.to_datetime(until_date) if until_date else today

            # Define safety keywords
            safety_keywords = [
                'safety precautions', 'temporary electricity', 'on-site labor is working without wearing safety belt',
                'safety norms', 'Missing Cabin Glass – Tower Crane', 'Crane Operator cabin front glass',
                'site on priority basis lifeline is not fixed at the working place',
                'operated only after Third Party Inspection and certification crane operated without TPIC',
                'safety precautions are not taken seriously at site Tower crane operator cabin front glass is missing while crane operator is working inside cabin',
                'no barrier around', 'Lock and Key arrangement to restrict unauthorized operations, buzzer while operation, gates at landing platforms, catch net in the vicinity',
                'safety precautions are not taken seriously', 'firecase', 'Health and Safety Plan',
                'noticed that submission of statistics report is regularly delayed',
                'crane operator cabin front glass is missing while crane operator is working inside cabin',
                'labor is working without wearing safety belt', 'barricading', 'tank', 'safety shoes',
                'safety belt', 'helmet', 'lifeline', 'guard rails', 'fall protection', 'PPE', 'electrical hazard',
                'unsafe platform', 'catch net', 'edge protection', 'TPI', 'scaffold', 'lifting equipment',
                'dust suppression', 'debris chute', 'spill control', 'crane operator', 'halogen lamps',
                'fall catch net', 'environmental contamination', 'fire hazard',' continuous down slope movement of soil','continuous collapse of soil leading '
            ]

            def is_safety_record(description):
                if description is None or not isinstance(description, str):
                    logger.debug(f"Invalid description encountered: {description}")
                    return False
                description_lower = description.lower()
                matches = any(keyword in description_lower for keyword in safety_keywords)
                if not matches:
                    logger.debug(f"No safety keywords found in: {description}")
                return matches

            # Normalize site names to support combined tower Common Areas
            def normalize_site_name(description):
                desc_lower = description.lower() if isinstance(description, str) else ""
                # Handle tower-specific CommonArea (e.g., Eden-Tower-04-CommonArea or Eden-Tower-04-05-CommonArea)
                tower_common_match = re.search(r'(?:eden-)?tower-(\d+)(?:-(\d+))?-commonarea', desc_lower, re.IGNORECASE)
                if tower_common_match:
                    tower_num1 = tower_common_match.group(1).zfill(2)
                    tower_num2 = tower_common_match.group(2).zfill(2) if tower_common_match.group(2) else None
                    if tower_num2:
                        return (f"Eden-Tower {tower_num1}", f"Eden-Tower {tower_num2}")
                    else:
                        return f"Eden-Tower {tower_num1}"
                
                # Handle general CommonArea variations
                if "commonarea" in desc_lower or "common area" in desc_lower:
                    return "Common_Area"
                
                # Handle regular tower names
                tower_match = re.search(r"(?:eden-)?(?:tower|t)\s*-?\s*(\d+|2021|28)", desc_lower, re.IGNORECASE)
                if tower_match:
                    num = tower_match.group(1).zfill(2)
                    return f"Eden-Tower {num}"
                
                return "Common_Area"

            # Filter data
            if report_type == "Closed":
                st.write("Null check for critical columns:")
                st.write(df[['Days', 'Description', 'Created Date (WET)', 'Expected Close Date (WET)']].isnull().sum())

                filtered_df = df[
                    (
                        (df['Discipline'] == 'HSE') |
                        (df['Description'].apply(is_safety_record))
                    ) &
                    (df['Status'] == 'Closed') &
                    (df['Days'].notnull()) &
                    (df['Days'] > 7)
                ].copy()
                
                # Apply date filtering only if dates are provided
                if closed_start and closed_end:
                    filtered_df = filtered_df[
                        (filtered_df['Created Date (WET)'] >= closed_start) &
                        (pd.to_datetime(filtered_df['Expected Close Date (WET)'], errors='coerce') <= closed_end)
                    ].copy()
                    st.write(f"Records after date filtering ({closed_start} to {closed_end}):")
                    st.write(filtered_df)
                else:
                    st.warning("No date range provided for Closed report, including all records.")

                st.write(f"Filtered Closed DataFrame ({len(filtered_df)} records):")
                st.write(filtered_df[['Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status', 'Days']])

                if filtered_df.empty:
                    st.error("No records found for Closed Safety NCR report. Check date range or data.")
                    logger.info("No records found for Closed Safety NCR report.")
                    return {"Safety": {"Sites": {}, "Grand_Total": 0}}, ""
                
            else:  # Open
                st.write(report_type)
                
                # Initial filtering for open records
                filtered_df = df[
                    (
                        (df['Discipline'] == 'HSE') |
                        (df['Description'].apply(is_safety_record))
                    ) &
                    (df['Status'] == 'Open') &
                    (pd.to_datetime(df['Created Date (WET)']).notna())
                ].copy()
                filtered_df.loc[:, 'Days_From_Today'] = (today - pd.to_datetime(filtered_df['Created Date (WET)'])).dt.days
                filtered_df = filtered_df[filtered_df['Days_From_Today'] > 7].copy()
                if open_until:
                    filtered_df = filtered_df[
                        (pd.to_datetime(filtered_df['Created Date (WET)']) <= open_until)
                    ].copy()

            if filtered_df.empty:
                return {"Safety": {"Sites": {}, "Grand_Total": 0}}, ""

            filtered_df.loc[:, 'Created Date (WET)'] = pd.to_datetime(filtered_df['Created Date (WET)']).dt.strftime('%Y-%m-%d')
            filtered_df.loc[:, 'Expected Close Date (WET)'] = pd.to_datetime(filtered_df['Expected Close Date (WET)']).dt.strftime('%Y-%m-%d')

            processed_data = filtered_df.to_dict(orient="records")
    
            cleaned_data = []
            seen_descriptions = set()
            # Define standard_sites before use
            standard_sites = [
                "Eden-Tower 04", "Eden-Tower 05", "Eden-Tower 06", "Eden-Tower 07", "Common_Area"
            ]
            for record in processed_data:
                description = str(record.get("Description", "")).strip()
                if description and description not in seen_descriptions:
                    seen_descriptions.add(description)
                    cleaned_record = {
                        "Description": description,
                        "Created Date (WET)": str(record.get("Created Date (WET)", "")),
                        "Expected Close Date (WET)": str(record.get("Expected Close Date (WET)", "")),
                        "Status": str(record.get("Status", "")),
                        "Days": record.get("Days", 0),
                        "Discipline": "HSE"
                    }
                    if report_type == "Open":
                        cleaned_record["Days_From_Today"] = record.get("Days_From_Today", 0)
                    # Assign tower(s) based on description using normalize_site_name
                    normalized_towers = normalize_site_name(description)
                    if isinstance(normalized_towers, tuple):
                        # Create a record for each tower in the combined CommonArea
                        for tower in normalized_towers:
                            tower_record = cleaned_record.copy()
                            tower_record["Tower"] = tower
                            cleaned_data.append(tower_record)
                            logger.debug(f"Tower set to {tower}")
                    else:
                        cleaned_record["Tower"] = normalized_towers
                        cleaned_data.append(cleaned_record)
                        logger.debug(f"Tower set to {normalized_towers}")

            st.write(f"Total {report_type} records to process: {len(cleaned_data)}")

            if not cleaned_data:
                logger.info("No safety records after deduplication")
                return {"Safety": {"Sites": {}, "Grand_Total": 0}}, ""

            result = {"Safety": {"Sites": {}, "Grand_Total": 0}}

            if debug_bypass_api:
                logger.info("Bypassing WatsonX API for debugging")
                for record in cleaned_data:
                    if is_safety_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Reference", 0) > 7) and record.get("Discipline") == "HSE":
                        site = record["Tower"]
                        if site not in result["Safety"]["Sites"]:
                            result["Safety"]["Sites"][site] = {
                                "Count": 0,
                                "Descriptions": [],
                                "Created Date (WET)": [],
                                "Expected Close Date (WET)": [],
                                "Status": []
                            }
                        result["Safety"]["Sites"][site]["Descriptions"].append(record["Description"])
                        result["Safety"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                        result["Safety"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                        result["Safety"]["Sites"][site]["Status"].append(record["Status"])
                        result["Safety"]["Sites"][site]["Count"] += 1
                        result["Safety"]["Grand_Total"] += 1
                logger.debug(f"Debug result: {json.dumps(result, indent=2)}")
                return result, json.dumps(result)

            access_token = get_access_token(API_KEY)
            if not access_token:
                return {"error": "Failed to obtain access token"}, ""

            chunk_size = 10
            total_chunks = (len(cleaned_data) + chunk_size - 1) // chunk_size

            session = requests.Session()
            retry_strategy = Retry(
                total=3,
                backoff_factor=2,
                status_forcelist=[500, 502, 503, 504, 429, 408],
                allowed_methods=["POST"],
                raise_on_redirect=True,
                raise_on_status=True
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)

            progress_placeholder = st.empty()
            status_placeholder = st.empty()
            error_placeholder = st.empty()
            progress_bar = progress_placeholder.progress(0)

            for i in range(0, len(cleaned_data), chunk_size):
                chunk = cleaned_data[i:i + chunk_size]
                current_chunk = i // chunk_size + 1
                progress = min((current_chunk / total_chunks) * 100, 100)
                progress_bar.progress(int(progress))
                status_placeholder.write(f"Processed {current_chunk}/{total_chunks} chunks ({int(progress)}%)")
                logger.debug(f"Chunk data: {json.dumps(chunk, indent=2)}")

                prompt = (
                    "Generate EXACTLY ONE JSON object matching the format below. Do not repeat input data, generate multiple objects, include code, explanations, or code blocks. "
                    f"Count Safety NCRs by 'Tower' where 'Discipline' is 'HSE' and {'Days' if report_type == 'Closed' else 'Days_From_Reference'} > 7. "
                    "Descriptions must contain these keywords (case-insensitive): "
                    "'safety precautions', 'temporary electricity', 'on-site labor is working without wearing safety belt', 'safety norms', 'Missing Cabin Glass – Tower Crane', 'Crane Operator cabin front glass', "
                    "'site on priority basis lifeline is not fixed at the working place', 'operated only after Third Party Inspection and certification crane operated without TPIC', "
                    "'safety precautions are not taken seriously at site Tower crane operator cabin front glass is missing while crane operator is working inside cabin', "
                    "'no barrier around', 'Lock and Key arrangement to restrict unauthorized operations, buzzer while operation, gates at landing platforms, catch net in the vicinity', "
                    "'safety precautions are not taken seriously', 'firecase', 'Health and Safety Plan', 'noticed that submission of statistics report is regularly delayed', "
                    "'crane operator cabin front glass is missing while crane operator is working inside cabin', 'labor is working without wearing safety belt', 'barricading', 'tank', 'safety shoes', "
                    "'safety belt', 'helmet', 'lifeline', 'guard rails', 'fall protection', 'PPE', 'electrical hazard', 'unsafe platform', 'catch net', 'edge protection', 'TPI', 'scaffold', "
                    "'lifting equipment', 'dust suppression', 'debris chute', 'spill control', 'crane operator', 'halogen lamps', 'fall catch net', 'environmental contamination', 'fire hazard','continuous collapse of soil leading to instability','continuous down slope movement of soil'.\n\n"
                    "Group by 'Tower' (e.g., 'Eden-Tower 06', 'Common_Area'). Include all input sites, even with count 0. Collect 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status' in arrays. Set 'Count' to the number of matching NCRs.\n\n"
                    "Output Format:\n"
                    "{\n"
                    '  "Safety": {\n'
                    '    "Sites": {\n'
                    '      "Site_Name": {\n'
                    '        "Descriptions": [],\n'
                    '        "Created Date (WET)": [],\n'
                    '        "Expected Close Date (WET)": [],\n'
                    '        "Status": [],\n'
                    '        "Count": 0\n'
                    '      }\n'
                    '    },\n'
                    '    "Grand_Total": 0\n'
                    '  }\n'
                    '}\n\n'
                    f"Input Data: {json.dumps(chunk)}\n"
                )

                payload = {
                    "input": prompt,
                    "parameters": {
                        "decoding_method": "greedy",
                        "max_new_tokens": 300,
                        "min_new_tokens": 0,
                        "temperature": 0.001,
                        "n": 1
                    },
                    "model_id": MODEL_ID,
                    "project_id": PROJECT_ID
                }
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {access_token}"
                }

                try:
                    logger.debug("Initiating WatsonX API call...")
                    response = session.post(WATSONX_API_URL, headers=headers, json=payload, verify=certifi.where(), timeout=30)
                    logger.info(f"WatsonX API response status: {response.status_code}")

                    if response.status_code == 200:
                        api_result = response.json()
                        generated_text = api_result.get("results", [{}])[0].get("generated_text", "").strip()
                        logger.debug(f"Generated text for chunk {current_chunk}: {generated_text}")

                        json_str = None
                        brace_count = 0
                        start_idx = None
                        for idx, char in enumerate(generated_text):
                            if char == '{':
                                if brace_count == 0:
                                    start_idx = idx
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0 and start_idx is not None:
                                    json_str = generated_text[start_idx:idx + 1]
                                    break

                        if json_str:
                            try:
                                logger.debug(f"Extracted JSON string: {json_str}")
                                parsed_json = json.loads(json_str)
                                chunk_result = parsed_json.get("Safety", {})
                                chunk_sites = chunk_result.get("Sites", {})
                                chunk_grand_total = chunk_result.get("Grand_Total", 0)

                                for site, values in chunk_sites.items():
                                    if not isinstance(values, dict):
                                        logger.warning(f"Invalid site data for {site}: {values}")
                                        continue
                                    if site not in result["Safety"]["Sites"]:
                                        result["Safety"]["Sites"][site] = {
                                            "Count": 0,
                                            "Descriptions": [],
                                            "Created Date (WET)": [],
                                            "Expected Close Date (WET)": [],
                                            "Status": []
                                        }
                                    result["Safety"]["Sites"][site]["Descriptions"].extend(values.get("Descriptions", []))
                                    result["Safety"]["Sites"][site]["Created Date (WET)"].extend(values.get("Created Date (WET)", []))
                                    result["Safety"]["Sites"][site]["Expected Close Date (WET)"].extend(values.get("Expected Close Date (WET)", []))
                                    result["Safety"]["Sites"][site]["Status"].extend(values.get("Status", []))
                                    result["Safety"]["Sites"][site]["Count"] += values.get("Count", 0)
                                result["Safety"]["Grand_Total"] += chunk_grand_total
                                logger.debug(f"Successfully processed chunk {current_chunk}/{total_chunks}")
                            except json.JSONDecodeError as e:
                                logger.error(f"JSONDecodeError for chunk {current_chunk}: {str(e)}")
                                error_placeholder.error(f"Failed to parse JSON for chunk {current_chunk}: {str(e)}")
                                for record in chunk:
                                    if is_safety_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Reference", 0) > 7) and record.get("Discipline") == "HSE":
                                        site = record["Tower"]
                                        if site not in result["Safety"]["Sites"]:
                                            result["Safety"]["Sites"][site] = {
                                                "Count": 0,
                                                "Descriptions": [],
                                                "Created Date (WET)": [],
                                                "Expected Close Date (WET)": [],
                                                "Status": []
                                            }
                                        result["Safety"]["Sites"][site]["Descriptions"].append(record["Description"])
                                        result["Safety"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                                        result["Safety"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                                        result["Safety"]["Sites"][site]["Status"].append(record["Status"])
                                        result["Safety"]["Sites"][site]["Count"] += 1
                                        result["Safety"]["Grand_Total"] += 1
                        else:
                            logger.error(f"No valid JSON for chunk {current_chunk}: {generated_text}")
                            error_placeholder.error(f"No valid JSON for chunk {current_chunk}")
                            for record in chunk:
                                if is_safety_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Reference", 0) > 7) and record.get("Discipline") == "HSE":
                                    site = record["Tower"]
                                    if site not in result["Safety"]["Sites"]:
                                        result["Safety"]["Sites"][site] = {
                                            "Count": 0,
                                            "Descriptions": [],
                                            "Created Date (WET)": [],
                                            "Expected Close Date (WET)": [],
                                            "Status": []
                                        }
                                    result["Safety"]["Sites"][site]["Descriptions"].append(record["Description"])
                                    result["Safety"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                                    result["Safety"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                                    result["Safety"]["Sites"][site]["Status"].append(record["Status"])
                                    result["Safety"]["Sites"][site]["Count"] += 1
                                    result["Safety"]["Grand_Total"] += 1
                    else:
                        logger.error(f"WatsonX API error for chunk {current_chunk}: {response.status_code} - {response.text}")
                        error_placeholder.error(f"WatsonX API error for chunk {current_chunk}: {response.status_code}")
                        for record in chunk:
                            if is_safety_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Reference", 0) > 7) and record.get("Discipline") == "HSE":
                                site = record["Tower"]
                                if site not in result["Safety"]["Sites"]:
                                    result["Safety"]["Sites"][site] = {
                                        "Count": 0,
                                        "Descriptions": [],
                                        "Created Date (WET)": [],
                                        "Expected Close Date (WET)": [],
                                        "Status": []
                                    }
                                result["Safety"]["Sites"][site]["Descriptions"].append(record["Description"])
                                result["Safety"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                                result["Safety"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                                result["Safety"]["Sites"][site]["Status"].append(record["Status"])
                                result["Safety"]["Sites"][site]["Count"] += 1
                                result["Safety"]["Grand_Total"] += 1
                except requests.exceptions.ReadTimeout as e:
                    logger.error(f"ReadTimeoutError for chunk {current_chunk}: {str(e)}")
                    error_placeholder.error(f"Failed to connect to WatsonX API for chunk {current_chunk}: {str(e)}")
                    for record in chunk:
                        if is_safety_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Reference", 0) > 7) and record.get("Discipline") == "HSE":
                            site = record["Tower"]
                            if site not in result["Safety"]["Sites"]:
                                result["Safety"]["Sites"][site] = {
                                    "Count": 0,
                                    "Descriptions": [],
                                    "Created Date (WET)": [],
                                    "Expected Close Date (WET)": [],
                                    "Status": []
                                }
                            result["Safety"]["Sites"][site]["Descriptions"].append(record["Description"])
                            result["Safety"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                            result["Safety"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                            result["Safety"]["Sites"][site]["Status"].append(record["Status"])
                            result["Safety"]["Sites"][site]["Count"] += 1
                            result["Safety"]["Grand_Total"] += 1
                except requests.exceptions.RequestException as e:
                    logger.error(f"RequestException for chunk {current_chunk}: {str(e)}")
                    error_placeholder.error(f"Failed to connect to WatsonX API for chunk {current_chunk}: {str(e)}")
                    for record in chunk:
                        if is_safety_record(record["Description"]) and (record.get("Days", 0) > 7 or record.get("Days_From_Reference", 0) > 7) and record.get("Discipline") == "HSE":
                            site = record["Tower"]
                            if site not in result["Safety"]["Sites"]:
                                result["Safety"]["Sites"][site] = {
                                    "Count": 0,
                                    "Descriptions": [],
                                    "Created Date (WET)": [],
                                    "Expected Close Date (WET)": [],
                                    "Status": []
                                }
                            result["Safety"]["Sites"][site]["Descriptions"].append(record["Description"])
                            result["Safety"]["Sites"][site]["Created Date (WET)"].append(record["Created Date (WET)"])
                            result["Safety"]["Sites"][site]["Expected Close Date (WET)"].append(record["Expected Close Date (WET)"])
                            result["Safety"]["Sites"][site]["Status"].append(record["Status"])
                            result["Safety"]["Sites"][site]["Count"] += 1
                            result["Safety"]["Grand_Total"] += 1

            progress_bar.progress(100)
            status_placeholder.write(f"Processed {total_chunks}/{total_chunks} chunks (100%)")
            logger.debug(f"Final result before deduplication: {json.dumps(result, indent=2)}")

            for site in result["Safety"]["Sites"]:
                result["Safety"]["Sites"][site]["Descriptions"] = list(set(result["Safety"]["Sites"][site]["Descriptions"]))
                result["Safety"]["Sites"][site]["Created Date (WET)"] = list(set(result["Safety"]["Sites"][site]["Created Date (WET)"]))
                result["Safety"]["Sites"][site]["Expected Close Date (WET)"] = list(set(result["Safety"]["Sites"][site]["Expected Close Date (WET)"]))
                result["Safety"]["Sites"][site]["Status"] = list(set(result["Safety"]["Sites"][site]["Status"]))
            
            logger.debug(f"Final result after deduplication: {json.dumps(result, indent=2)}")
            return result, json.dumps(result)
        except Exception as e:
            logger.error(f"Unexpected error in generate_ncr_Safety_report: {str(e)}")
            st.error(f"❌ Unexpected Error: {str(e)}")
            return {"error": f"Unexpected Error: {str(e)}"}, ""
 

@st.cache_data
def generate_consolidated_ncr_Housekeeping_excel_for_eden(combined_result, report_title="Housekeeping: Current Month"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        title_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'fg_color': 'yellow', 'border': 1, 'font_size': 12
        })
        header_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        cell_format = workbook.add_format({
            'align': 'center', 'valign': 'vcenter', 'border': 1
        })
        site_format = workbook.add_format({
            'align': 'left', 'valign': 'vcenter', 'border': 1
        })
        description_format = workbook.add_format({
            'align': 'left', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        
        report_type = "Closed" if "Closed" in report_title else "Open"
        now = datetime.now()
        day = now.strftime("%d")
        month_name = now.strftime("%B")
        year = now.strftime("%Y")
        date_part = f"{day}_{month_name}_{year}"
        report_title = f"Housekeeping: {report_type} - {date_part}"

        def truncate_sheet_name(base_name, max_length=31):
            if len(base_name) > max_length:
                return base_name[:max_length - 3] + "..."
            return base_name

        summary_sheet_name = truncate_sheet_name(f'Housekeeping NCR Report {date_part}')
        details_sheet_name = truncate_sheet_name(f'Housekeeping NCR Details {date_part}')

        worksheet_summary = workbook.add_worksheet(summary_sheet_name)
        worksheet_summary.set_column('A:A', 20)
        worksheet_summary.set_column('B:B', 15)
        
        data = combined_result.get("Housekeeping", {}).get("Sites", {})
        
        standard_sites = [
            "Eden-Tower 04", "Eden-Tower 05", "Eden-Tower 06", "Eden-Tower 07", "Common_Area"
        ]
        
        def normalize_site_name(site):
            if site == "Common_Area":
                return site
            if "CommonArea" in site or "Common Area" in site:
                return "Common_Area"
            match = re.search(r'(?:eden-)?(?:tower|t)[- ]?(\d+)', site, re.IGNORECASE)
            if match:
                num = match.group(1).zfill(2)
                return f"Eden-Tower {num}"
            return site

        site_mapping = {k: normalize_site_name(k) for k in data.keys()}
        sorted_sites = sorted(standard_sites)
        
        worksheet_summary.merge_range('A1:B1', report_title, title_format)
        row = 1
        worksheet_summary.write(row, 0, 'Site', header_format)
        worksheet_summary.write(row, 1, 'No. of Housekeeping NCRs beyond 7 days', header_format)
        
        row = 2
        for site in sorted_sites:
            worksheet_summary.write(row, 0, site, site_format)
            original_key = next((k for k, v in site_mapping.items() if v == site), None)
            if original_key and original_key in data:
                value = data[original_key].get("Count", 0)
            else:
                value = 0
            worksheet_summary.write(row, 1, value, cell_format)
            row += 1
        
        worksheet_details = workbook.add_worksheet(details_sheet_name)
        worksheet_details.set_column('A:A', 20)
        worksheet_details.set_column('B:B', 60)
        worksheet_details.set_column('C:D', 20)
        worksheet_details.set_column('E:E', 15)
        worksheet_details.set_column('F:F', 15)
        
        worksheet_details.merge_range('A1:F1', f"{report_title} - Details", title_format)
        
        headers = ['Site', 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status', 'Discipline']
        row = 1
        for col, header in enumerate(headers):
            worksheet_details.write(row, col, header, header_format)
        
        row = 2
        for site in sorted_sites:
            original_key = next((k for k, v in site_mapping.items() if v == site), None)
            if original_key and original_key in data:
                site_data = data[original_key]
                descriptions = site_data.get("Descriptions", [])
                created_dates = site_data.get("Created Date (WET)", [])
                close_dates = site_data.get("Expected Close Date (WET)", [])
                statuses = site_data.get("Status", [])
                max_length = max(len(descriptions), len(created_dates), len(close_dates), len(statuses))
                for i in range(max_length):
                    worksheet_details.write(row, 0, site, site_format)
                    worksheet_details.write(row, 1, descriptions[i] if i < len(descriptions) else "", description_format)
                    worksheet_details.write(row, 2, created_dates[i] if i < len(created_dates) else "", cell_format)
                    worksheet_details.write(row, 3, close_dates[i] if i < len(close_dates) else "", cell_format)
                    worksheet_details.write(row, 4, statuses[i] if i < len(statuses) else "", cell_format)
                    worksheet_details.write(row, 5, "HSE", cell_format)
                    row += 1
        
        output.seek(0)
        return output
    
@st.cache_data
def generate_consolidated_ncr_Safety_excel_for_eden(combined_result, report_title=None):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        title_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'fg_color': 'yellow', 'border': 1, 'font_size': 12
        })
        header_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        cell_format = workbook.add_format({
            'align': 'center', 'valign': 'vcenter', 'border': 1
        })
        site_format = workbook.add_format({
            'align': 'left', 'valign': 'vcenter', 'border': 1
        })
        description_format = workbook.add_format({
            'align': 'left', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        
        now = datetime.now()
        day = now.strftime("%d")
        month_name = now.strftime("%B")
        year = now.strftime("%Y")
        date_part = f"{month_name} {day}, {year}"
        if report_title is None:
            report_title = f"Safety: {date_part} - Current Month"
        else:
            report_type = "Safety"
            report_title = f"{date_part}: {report_type}"

        def truncate_sheet_name(base_name, max_length=31):
            if len(base_name) > max_length:
                return base_name[:max_length - 3] + "..."
            return base_name

        summary_sheet_name = truncate_sheet_name(f'Safety NCR Report {date_part}')
        details_sheet_name = truncate_sheet_name(f'Safety NCR Details {date_part}')

        worksheet_summary = workbook.add_worksheet(summary_sheet_name)
        worksheet_summary.set_column('A:A', 20)
        worksheet_summary.set_column('B:B', 15)
        
        data = combined_result.get("Safety", {}).get("Sites", {})
        
        standard_sites = [
            "Eden-Tower 04", "Eden-Tower 05", "Eden-Tower 06", "Eden-Tower 07", "Common_Area"
        ]
        
        def normalize_site_name(site):
            if site == "Common_Area":
                return site
            if "CommonArea" in site or "Common Area" in site:
                return "Common_Area"
            match = re.search(r'(?:eden-)?(?:tower|t)[- ]?(\d+)', site, re.IGNORECASE)
            if match:
                num = match.group(1).zfill(2)
                return f"Eden-Tower {num}"
            return site

        site_mapping = {k: normalize_site_name(k) for k in data.keys()}
        sorted_sites = sorted(standard_sites)
        
        worksheet_summary.merge_range('A1:B1', report_title, title_format)
        row = 1
        worksheet_summary.write(row, 0, 'Site', header_format)
        worksheet_summary.write(row, 1, 'No. of Safety NCRs beyond 7 days', header_format)
        
        row = 2
        for site in sorted_sites:
            worksheet_summary.write(row, 0, site, site_format)
            original_key = next((k for k, v in site_mapping.items() if v == site), None)
            if original_key and original_key in data:
                value = data[original_key].get("Count", 0)
            else:
                value = 0
            worksheet_summary.write(row, 1, value, cell_format)
            row += 1
        
        worksheet_details = workbook.add_worksheet(details_sheet_name)
        worksheet_details.set_column('A:A', 20)
        worksheet_details.set_column('B:B', 60)
        worksheet_details.set_column('C:D', 20)
        worksheet_details.set_column('E:E', 15)
        worksheet_details.set_column('F:F', 15)

        worksheet_details.merge_range('A1:F1', f"{report_title} - Details", title_format)
        
        headers = ['Site', 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status', 'Discipline']
        row = 1
        for col, header in enumerate(headers):
            if col == 5:
                worksheet_details.write(row, col, header, title_format)
            else:
                worksheet_details.write(row, col, header, header_format)
        
        row = 2
        for site in sorted_sites:
            original_key = next((k for k, v in site_mapping.items() if v == site), None)
            if original_key and original_key in data:
                site_data = data[original_key]
                descriptions = site_data.get("Descriptions", [])
                created_dates = site_data.get("Created Date (WET)", [])
                close_dates = site_data.get("Expected Close Date (WET)", [])
                statuses = site_data.get("Status", [])
                max_length = max(len(descriptions), len(created_dates), len(close_dates), len(statuses))
                for i in range(max_length):
                    worksheet_details.write(row, 0, site, site_format)
                    worksheet_details.write(row, 1, descriptions[i] if i < len(descriptions) else "", description_format)
                    worksheet_details.write(row, 2, created_dates[i] if i < len(created_dates) else "", cell_format)
                    worksheet_details.write(row, 3, close_dates[i] if i < len(close_dates) else "", cell_format)
                    worksheet_details.write(row, 4, statuses[i] if i < len(statuses) else "", cell_format)
                    worksheet_details.write(row, 5, "HSE", cell_format)
                    row += 1
        
        output.seek(0)
        return output
 


@st.cache_data
def generate_consolidated_ncr_OpenClose_excel_for_eden(combined_result, report_title="NCR"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # Define formatting styles
        title_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'fg_color': 'yellow', 'border': 1, 'font_size': 12
        })
        header_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        subheader_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1
        })
        cell_format_base = {
            'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        }
        site_format_base = {
            'align': 'left', 'valign': 'vcenter', 'border': 1
        }
        
        # Watercolor-style colors for each tower
        tower_colors = {
            "Tower 4": '#DCE6F1',  # Light blue
            "Tower 5": '#DCE6F1',  # Light blue
            "Tower 6": '#DCE6F1',  # Light blue
            "Tower 7": '#DCE6F1',  # Light blue
            "Common_Area": '#DCE6F1'  # Light blue
        }
        
        # Create format dictionaries for each tower
        tower_formats = {}
        for site, color in tower_colors.items():
            tower_formats[site] = {
                'tower_total': workbook.add_format({
                    'bold': True, 'align': 'left', 'valign': 'vcenter', 'border': 1, 'fg_color': color
                }),
                'site': workbook.add_format({
                    'align': 'left', 'valign': 'vcenter', 'border': 1, 'fg_color': '#FFFFFF'
                }),
                'cell': workbook.add_format({
                    'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True, 'fg_color': '#FFFFFF'
                })
            }
        
        default_cell_format = workbook.add_format(cell_format_base)
        default_site_format = workbook.add_format(site_format_base)
        
        # Generate date string
        now = datetime.now()
        day = now.strftime("%d")
        month_name = now.strftime("%B")
        year = now.strftime("%Y")
        date_part = f"{day}_{month_name}_{year}"
        
        def truncate_sheet_name(base_name, max_length=31):
            if len(base_name) > max_length:
                return base_name[:max_length - 3] + "..."
            return base_name

        worksheet = workbook.add_worksheet('NCR Report')
        worksheet.set_column('A:A', 20)
        worksheet.set_column('B:H', 12)
        
        # Extract data
        resolved_data = combined_result.get("NCR resolved beyond 21 days", {})
        open_data = combined_result.get("NCR open beyond 21 days", {})
        if not isinstance(resolved_data, dict) or "error" in resolved_data:
            resolved_data = {"Sites": {}}
        if not isinstance(open_data, dict) or "error" in open_data:
            open_data = {"Sites": {}}
            
        resolved_sites = resolved_data.get("Sites", {})
        open_sites = open_data.get("Sites", {})
        
        standard_sites = [
            "Tower 4", "Tower 5", "Tower 6", "Tower 7",
            "Common_Area"
        ]
        
        def normalize_site_name(site):
            # Handle tower-specific CommonArea (e.g., Eden-Tower-04-CommonArea or Eden-Tower-04-05-CommonArea)
            tower_common_match = re.search(r'(?:eden-)?tower-(\d+)(?:-(\d+))?-commonarea', site, re.IGNORECASE)
            if tower_common_match:
                # If it's a combined tower CommonArea (e.g., Eden-Tower-04-05-CommonArea)
                tower_num1 = tower_common_match.group(1)
                tower_num2 = tower_common_match.group(2)
                if tower_num2:
                    # Return a tuple of tower names to handle multiple towers
                    return (f"Tower {int(tower_num1)}", f"Tower {int(tower_num2)}")
                else:
                    # Single tower CommonArea (e.g., Eden-Tower-04-CommonArea)
                    return f"Tower {int(tower_num1)}"
            
            # Handle general CommonArea variations
            if "CommonArea" in site or "Common Area" in site:
                return "Common_Area"
            
            # Handle Eden-Tower-XX format
            if site.startswith("Eden-Tower-"):
                tower_num = site.split("Eden-Tower-")[1]
                if tower_num.isdigit():
                    return f"Tower {int(tower_num)}"
            
            # Handle regular tower names
            tower_match = re.search(r'(?:eden-)?(?:tower|t)[- ]?(\d+)', site, re.IGNORECASE)
            if tower_match:
                num = int(tower_match.group(1))
                return f"Tower {num}"
            
            return site

        # Create mapping for all sites
        all_sites = set(resolved_sites.keys()) | set(open_sites.keys())
        site_mapping = {}
        for k in all_sites:
            normalized = normalize_site_name(k)
            if isinstance(normalized, tuple):
                # For combined tower CommonAreas, map to both towers
                for tower in normalized:
                    site_mapping.setdefault(k, []).append(tower)
            else:
                site_mapping[k] = [normalized]
        
        # Reverse mapping for finding original keys
        def find_original_keys(normalized_site):
            return [k for k, v in site_mapping.items() if normalized_site in v]
        
        def map_pour_to_level(pour_name):
            """Map various pour name formats to standardized pour levels"""
            if not pour_name:
                return "common"
            
            pour_str = str(pour_name).strip().lower()
            
            # Handle Module X format
            if pour_str.startswith("module "):
                try:
                    module_num = int(pour_str.split(" ")[1])
                    if 1 <= module_num <= 2:  # Assuming 2 pour levels
                        return f"Pour {module_num}"
                except (IndexError, ValueError):
                    pass
            
            # Handle Pour X format
            elif pour_str.startswith("pour "):
                try:
                    pour_num = int(pour_str.split(" ")[1])
                    if 1 <= pour_num <= 2:  # Assuming 2 pour levels
                        return f"Pour {pour_num}"
                except (IndexError, ValueError):
                    pass
            
            # Handle direct numbers
            elif pour_str.isdigit():
                pour_num = int(pour_str)
                if 1 <= pour_num <= 2:
                    return f"Pour {pour_num}"
            
            # Handle common variations
            elif pour_str in ["common", "general", "misc", "miscellaneous", ""]:
                return "common"
            
            # Default to common for unrecognized formats
            return "common"
        
        # Write header
        worksheet.merge_range('A1:H1', f"{report_title} {date_part}", title_format)
        row = 1
        worksheet.write(row, 0, 'Site', header_format)
        worksheet.merge_range(row, 1, row, 3, 'NCR resolved beyond 21 days', header_format)
        worksheet.merge_range(row, 4, row, 6, 'NCR open beyond 21 days', header_format)
        worksheet.write(row, 7, 'Total', header_format)
        
        row = 2
        categories = ['Civil Finishing', 'Structure Works', 'MEP']
        worksheet.write(row, 0, '', header_format)
        for i, cat in enumerate(categories):
            worksheet.write(row, i+1, cat, subheader_format)
        for i, cat in enumerate(categories):
            worksheet.write(row, i+4, cat, subheader_format)
        worksheet.write(row, 7, '', header_format)
        
        # Define pour levels
        pour_levels = [f"Pour {i}" for i in range(1, 3)]  # ['Pour 1', 'Pour 2']
        
        row = 3
        site_totals = {}
        
        for site in standard_sites:
            formats = tower_formats.get(site, {})
            tower_total_format = formats.get('tower_total', workbook.add_format({
                'bold': True, 'align': 'left', 'valign': 'vcenter', 'border': 1, 'fg_color': '#D3D3D3'
            }))
            site_format = formats.get('site', default_site_format)
            cell_format = formats.get('cell', default_cell_format)
            
            resolved_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
            open_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
            
            # Find all original keys that map to this normalized site
            original_keys = find_original_keys(site)
            
            if site == "Common_Area":
                # Handle Common_Area specifically (only for non-tower-specific Common Areas)
                for original_key in original_keys:
                    # Skip tower-specific CommonAreas
                    if re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE):
                        continue
                        
                    # Resolved data
                    if original_key in resolved_sites:
                        site_data = resolved_sites[original_key]
                        disciplines = site_data.get("Discipline", [])
                        for discipline in disciplines:
                            if discipline == 'SW':
                                resolved_counts['Structure Works'] += 1
                            elif discipline == 'FW':
                                resolved_counts['Civil Finishing'] += 1
                            elif discipline in ['MEP', 'HSE']:
                                resolved_counts['MEP'] += 1
                    
                    # Open data
                    if original_key in open_sites:
                        site_data = open_sites[original_key]
                        disciplines = site_data.get("Discipline", [])
                        for discipline in disciplines:
                            if discipline == 'SW':
                                open_counts['Structure Works'] += 1
                            elif discipline == 'FW':
                                open_counts['Civil Finishing'] += 1
                            elif discipline in ['MEP', 'HSE']:
                                open_counts['MEP'] += 1
            
            else:  # Tower sites
                resolved_pour_counts = {level: {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0} for level in pour_levels}
                open_pour_counts = {level: {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0} for level in pour_levels}
                resolved_common_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
                open_common_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
                
                # Process each original key that maps to this tower
                for original_key in original_keys:
                    # Process regular tower data (not CommonArea)
                    if not re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE) and "CommonArea" not in original_key:
                        # Process resolved data
                        if original_key in resolved_sites:
                            site_data = resolved_sites[original_key]
                            disciplines = site_data.get("Discipline", [])
                            pours = site_data.get("Pours", [])
                            
                            for i, discipline in enumerate(disciplines):
                                if discipline == 'SW':
                                    cat = 'Structure Works'
                                elif discipline == 'FW':
                                    cat = 'Civil Finishing'
                                elif discipline in ['MEP', 'HSE']:
                                    cat = 'MEP'
                                else:
                                    cat = 'Structure Works'
                                
                                pour_list = pours[i] if i < len(pours) else ['Common']
                                for pour in pour_list:
                                    pour_level = map_pour_to_level(pour)
                                    if pour_level in pour_levels:
                                        resolved_pour_counts[pour_level][cat] += 1
                                    else:
                                        resolved_common_counts[cat] += 1
                        
                        # Process open data
                        if original_key in open_sites:
                            site_data = open_sites[original_key]
                            disciplines = site_data.get("Discipline", [])
                            pours = site_data.get("Pours", [])
                            
                            for i, discipline in enumerate(disciplines):
                                if discipline == 'SW':
                                    cat = 'Structure Works'
                                elif discipline == 'FW':
                                    cat = 'Civil Finishing'
                                elif discipline in ['MEP', 'HSE']:
                                    cat = 'MEP'
                                else:
                                    cat = 'Structure Works'
                                
                                pour_list = pours[i] if i < len(pours) else ['Common']
                                for pour in pour_list:
                                    pour_level = map_pour_to_level(pour)
                                    if pour_level in pour_levels:
                                        open_pour_counts[pour_level][cat] += 1
                                    else:
                                        open_common_counts[cat] += 1
                    
                    # Process tower-specific CommonArea data
                    if re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE):
                        # Resolved data
                        if original_key in resolved_sites:
                            site_data = resolved_sites[original_key]
                            disciplines = site_data.get("Discipline", [])
                            for discipline in disciplines:
                                if discipline == 'SW':
                                    cat = 'Structure Works'
                                elif discipline == 'FW':
                                    cat = 'Civil Finishing'
                                elif discipline in ['MEP', 'HSE']:
                                    cat = 'MEP'
                                else:
                                    cat = 'Structure Works'
                                resolved_common_counts[cat] += 1
                        
                        # Open data
                        if original_key in open_sites:
                            site_data = open_sites[original_key]
                            disciplines = site_data.get("Discipline", [])
                            for discipline in disciplines:
                                if discipline == 'SW':
                                    cat = 'Structure Works'
                                elif discipline == 'FW':
                                    cat = 'Civil Finishing'
                                elif discipline in ['MEP', 'HSE']:
                                    cat = 'MEP'
                                else:
                                    cat = 'Structure Works'
                                open_common_counts[cat] += 1
                
                # Aggregate pour and CommonArea counts for tower total
                for cat in categories:
                    resolved_counts[cat] = sum(resolved_pour_counts[level][cat] for level in pour_levels) + resolved_common_counts[cat]
                    open_counts[cat] = sum(open_pour_counts[level][cat] for level in pour_levels) + open_common_counts[cat]
        
            site_total = sum(resolved_counts.values()) + sum(open_counts.values())
            
            # Write tower header row
            display_site = site if site == "Common_Area" else site
            worksheet.write(row, 0, display_site, tower_total_format)
            for i, cat in enumerate(categories):
                worksheet.write(row, i+1, resolved_counts[cat], cell_format)
            for i, cat in enumerate(categories):
                worksheet.write(row, i+4, open_counts[cat], cell_format)
            worksheet.write(row, 7, site_total, cell_format)
            site_totals[site] = site_total
            row += 1
            
            # Add pour rows for towers (not for Common_Area)
            if site != "Common_Area":
                for idx, level in enumerate(pour_levels, 1):
                    level_total = sum(resolved_pour_counts[level].values()) + sum(open_pour_counts[level].values())
                    worksheet.write(row, 0, f"Pour {idx}", site_format)
                    for i, display_cat in enumerate(categories):
                        worksheet.write(row, i+1, resolved_pour_counts[level][display_cat], cell_format)
                    for i, cat in enumerate(categories):
                        worksheet.write(row, i+4, open_pour_counts[level][cat], cell_format)
                    worksheet.write(row, 7, level_total, cell_format)
                    row += 1
                
                # Add tower-specific Common Description row
                common_total = sum(resolved_common_counts.values()) + sum(open_common_counts.values())
                common_desc = "Common Description"
                worksheet.write(row, 0, common_desc, site_format)
                for i, display_cat in enumerate(categories):
                    worksheet.write(row, i+1, resolved_common_counts[display_cat], cell_format)
                for i, cat in enumerate(categories):
                    worksheet.write(row, i+4, open_common_counts[cat], cell_format)
                worksheet.write(row, 7, common_total, cell_format)
                row += 1

        def write_detail_sheet(sheet_name, data, title):
            truncated_sheet_name = truncate_sheet_name(f"{sheet_name} {date_part}")
            detail_worksheet = workbook.add_worksheet(truncated_sheet_name)
            detail_worksheet.set_column('A:A', 20)
            detail_worksheet.set_column('B:B', 60)
            detail_worksheet.set_column('C:D', 20)
            detail_worksheet.set_column('E:E', 15)
            detail_worksheet.set_column('F:F', 15)
            detail_worksheet.merge_range('A1:F1', f"{title} {date_part}", title_format)
            headers = ['Site', 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status', 'Discipline']
            for col, detail in enumerate(headers):
                detail_worksheet.write(1, col, detail, header_format)
            row = 2
            for site, site_data in data.items():
                normalized_sites = site_mapping.get(site, [site])
                # For tower-specific CommonAreas, display under the respective tower(s)
                for normalized_site in normalized_sites:
                    display_site = normalized_site if normalized_site == "Common_Area" else normalized_site
                    descriptions = site_data.get("Descriptions", [])
                    created_dates = site_data.get("Created Date (WET)", [])
                    close_dates = site_data.get("Expected Close Date (WET)", [])
                    statuses = site_data.get("Status", [])
                    disciplines = site_data.get("Discipline", [])
                    max_length = max(len(descriptions), len(created_dates), len(close_dates), len(statuses), len(disciplines))
                    for i in range(max_length):
                        detail_worksheet.write(row, 0, display_site, default_site_format)
                        detail_worksheet.write(row, 1, descriptions[i] if i < len(descriptions) else "", default_cell_format)
                        detail_worksheet.write(row, 2, created_dates[i] if i < len(created_dates) else "", default_cell_format)
                        detail_worksheet.write(row, 3, close_dates[i] if i < len(close_dates) else "", default_cell_format)
                        detail_worksheet.write(row, 4, statuses[i] if i < len(statuses) else "", default_cell_format)
                        detail_worksheet.write(row, 5, disciplines[i] if i < len(disciplines) else "", default_cell_format)
                        row += 1

        if resolved_sites:
            write_detail_sheet("Closed NCR Details", resolved_sites, "Closed NCR Details")
        if open_sites:
            write_detail_sheet("Open NCR Details", open_sites, "Open NCR Details")

        output.seek(0)
        return output
    
@st.cache_data
def generate_combined_excel_report_for_eden(all_reports, filename_prefix="All_Reports"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # Formatting
        title_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'fg_color': 'yellow', 'border': 1, 'font_size': 12
        })
        header_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        subheader_format = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1
        })
        cell_format_base = {
            'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        }
        site_format_base = {
            'align': 'left', 'valign': 'vcenter', 'border': 1
        }
        
        # Watercolor-style colors for Combined NCR
        tower_colors = {
            "Eden-Tower 04": '#C4E4B7',  # Green
            "Eden-Tower 05": '#A3CFFA',  # Blue
            "Eden-Tower 06": '#F5C3C2',  # Pink
            "Eden-Tower 07": '#F5E8B7',  # Soft yellow
            "Common_Area": '#C7CEEA'     # Periwinkle
        }
        
        tower_formats = {}
        for site, color in tower_colors.items():
            tower_formats[site] = {
                'tower_total': workbook.add_format({
                    'bold': True, 'align': 'left', 'valign': 'vcenter', 'border': 1, 'fg_color': color
                }),
                'site': workbook.add_format({
                    'align': 'left', 'valign': 'vcenter', 'border': 1, 'fg_color': '#FFFFFF'
                }),
                'cell': workbook.add_format({
                    'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True, 'fg_color': '#FFFFFF'
                })
            }
        
        default_cell_format = workbook.add_format(cell_format_base)
        default_site_format = workbook.add_format(site_format_base)
        
        # Formatting for Safety/Housekeeping
        description_format = workbook.add_format({
            'align': 'left', 'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        
        # Date handling
        now = datetime.now()
        day = now.strftime("%d")
        month_name = now.strftime("%B")
        year = now.strftime("%Y")
        date_part = f"{day}_{month_name}_{year}"
        
        def truncate_sheet_name(base_name, max_length=31):
            if len(base_name) > max_length:
                return base_name[:max_length - 3] + "..."
            return base_name

        # Helper function to map pour names
        def map_pour_to_level(pour_name):
            if not pour_name:
                return "common"
            
            pour_str = str(pour_name).strip().lower()
            
            # Handle Module X format
            if pour_str.startswith("module "):
                try:
                    module_num = int(pour_str.split(" ")[1])
                    if 1 <= module_num <= 2:
                        return f"Pour {module_num}"
                except (IndexError, ValueError):
                    pass
            
            # Handle Pour X format
            elif pour_str.startswith("pour "):
                try:
                    pour_num = int(pour_str.split(" ")[1])
                    if 1 <= pour_num <= 2:
                        return f"Pour {pour_num}"
                except (IndexError, ValueError):
                    pass
            
            # Handle direct numbers
            elif pour_str.isdigit():
                pour_num = int(pour_str)
                if 1 <= pour_num <= 2:
                    return f"Pour {pour_num}"
            
            # Handle common variations
            elif pour_str in ["common", "general", "misc", "miscellaneous", ""]:
                return "common"
            
            return "common"

        # Modified normalize_site_name to support combined tower Common Areas
        def normalize_site_name(site):
            # Handle tower-specific CommonArea (e.g., Eden-Tower-04-CommonArea or Eden-Tower-04-05-CommonArea)
            tower_common_match = re.search(r'(?:eden-)?tower-(\d+)(?:-(\d+))?-commonarea', site, re.IGNORECASE)
            if tower_common_match:
                tower_num1 = tower_common_match.group(1).zfill(2)
                tower_num2 = tower_common_match.group(2).zfill(2) if tower_common_match.group(2) else None
                if tower_num2:
                    # Return a tuple for combined tower CommonArea
                    return (f"Eden-Tower {tower_num1}", f"Eden-Tower {tower_num2}")
                else:
                    # Single tower CommonArea
                    return f"Eden-Tower {tower_num1}"
            
            # Handle general CommonArea variations
            if "CommonArea" in site or "Common Area" in site:
                return "Common_Area"
            
            # Handle Eden-Tower-XX format
            if site.startswith("Eden-Tower-"):
                tower_num = site.split("Eden-Tower-")[1]
                if tower_num.isdigit():
                    return f"Eden-Tower {tower_num.zfill(2)}"
            
            # Handle regular tower names
            tower_match = re.search(r'(?:eden-)?(?:tower|t)[- ]?(\d+)', site, re.IGNORECASE)
            if tower_match:
                num = tower_match.group(1).zfill(2)
                return f"Eden-Tower {num}"
            
            return site

        # 1. Combined NCR Report
        combined_result = all_reports.get("Combined_NCR", {})
        report_title_ncr = f"NCR: {date_part}"
        
        worksheet = workbook.add_worksheet('NCR Report')
        worksheet.set_column('A:A', 20)
        worksheet.set_column('B:H', 12)
        
        resolved_data = combined_result.get("NCR resolved beyond 21 days", {})
        open_data = combined_result.get("NCR open beyond 21 days", {})
        if not isinstance(resolved_data, dict) or "error" in resolved_data:
            resolved_data = {"Sites": {}}
        if not isinstance(open_data, dict) or "error" in open_data:
            open_data = {"Sites": {}}
            
        resolved_sites = resolved_data.get("Sites", {})
        open_sites = open_data.get("Sites", {})
        
        standard_sites = [
            "Eden-Tower 04", "Eden-Tower 05", "Eden-Tower 06", "Eden-Tower 07", "Common_Area"
        ]
        
        # Create mapping for all sites
        all_sites = set(resolved_sites.keys()) | set(open_sites.keys())
        site_mapping = {}
        for k in all_sites:
            normalized = normalize_site_name(k)
            if isinstance(normalized, tuple):
                # For combined tower CommonAreas, map to both towers
                for tower in normalized:
                    site_mapping.setdefault(k, []).append(tower)
            else:
                site_mapping[k] = [normalized]
        
        # Helper function to find original keys
        def find_original_keys(normalized_site):
            return [k for k, v in site_mapping.items() if normalized_site in v]
        
        worksheet.merge_range('A1:H1', report_title_ncr, title_format)
        row = 1
        worksheet.write(row, 0, 'Site', header_format)
        worksheet.merge_range(row, 1, row, 3, 'NCR resolved beyond 21 days', header_format)
        worksheet.merge_range(row, 4, row, 6, 'NCR open beyond 21 days', header_format)
        worksheet.write(row, 7, 'Total', header_format)
        
        row = 2
        categories = ['Civil Finishing', 'Structure Works', 'MEP']
        worksheet.write(row, 0, '', header_format)
        for i, cat in enumerate(categories):
            worksheet.write(row, i+1, cat, subheader_format)
        for i, cat in enumerate(categories):
            worksheet.write(row, i+4, cat, subheader_format)
        worksheet.write(row, 7, '', header_format)
        
        category_map = {
            'Civil Finishing': ['FW', 'Civil Finishing', 'Finishing', 'Civil'],
            'Structure Works': ['SW', 'Structure Works', 'Structure', 'Works', 'Structural'],
            'MEP': ['MEP', 'Electrical', 'Mechanical', 'Plumbing', 'HSE']
        }
        pour_levels = ['Pour 1', 'Pour 2']
        row = 3
        site_totals = {}
        
        for site in standard_sites:
            formats = tower_formats.get(site, {})
            tower_total_format = formats.get('tower_total', workbook.add_format({
                'bold': True, 'align': 'left', 'valign': 'vcenter', 'border': 1, 'fg_color': '#D3D3D3'
            }))
            site_format = formats.get('site', default_site_format)
            cell_format = formats.get('cell', default_cell_format)
            
            resolved_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
            open_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
            
            # Find all original keys that map to this normalized site
            original_keys = find_original_keys(site)
            
            if site == "Common_Area":
                # Handle Common_Area specifically (only for non-tower-specific Common Areas)
                for original_key in original_keys:
                    # Skip tower-specific CommonAreas
                    if re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE):
                        continue
                        
                    # Resolved data
                    if original_key in resolved_sites:
                        disciplines = resolved_sites[original_key].get("Discipline", [])
                        for discipline in disciplines:
                            if discipline == 'FW':
                                resolved_counts['Civil Finishing'] += 1
                            elif discipline == 'SW':
                                resolved_counts['Structure Works'] += 1
                            elif discipline in ['MEP', 'HSE']:
                                resolved_counts['MEP'] += 1
                    
                    # Open data
                    if original_key in open_sites:
                        disciplines = open_sites[original_key].get("Discipline", [])
                        for discipline in disciplines:
                            if discipline == 'FW':
                                open_counts['Civil Finishing'] += 1
                            elif discipline == 'SW':
                                open_counts['Structure Works'] += 1
                            elif discipline in ['MEP', 'HSE']:
                                open_counts['MEP'] += 1
            
            else:  # Tower sites
                resolved_pour_counts = {level: {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0} for level in pour_levels}
                open_pour_counts = {level: {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0} for level in pour_levels}
                resolved_common_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
                open_common_counts = {'Civil Finishing': 0, 'Structure Works': 0, 'MEP': 0}
                
                # Process each original key that maps to this tower
                for original_key in original_keys:
                    # Process regular tower data (not CommonArea)
                    if not re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE) and "CommonArea" not in original_key:
                        # Resolved data
                        if original_key in resolved_sites:
                            disciplines = resolved_sites[original_key].get("Discipline", [])
                            pours = resolved_sites[original_key].get("Pours", [])
                            for i, discipline in enumerate(disciplines):
                                pour_list = pours[i] if i < len(pours) else ['Common']
                                cat = 'Civil Finishing' if discipline == 'FW' else 'Structure Works' if discipline == 'SW' else 'MEP'
                                for pour in pour_list:
                                    pour_level = map_pour_to_level(pour)
                                    if pour_level in pour_levels:
                                        resolved_pour_counts[pour_level][cat] += 1
                                    else:
                                        resolved_common_counts[cat] += 1
                        
                        # Open data
                        if original_key in open_sites:
                            disciplines = open_sites[original_key].get("Discipline", [])
                            pours = open_sites[original_key].get("Pours", [])
                            for i, discipline in enumerate(disciplines):
                                pour_list = pours[i] if i < len(pours) else ['Common']
                                cat = 'Civil Finishing' if discipline == 'FW' else 'Structure Works' if discipline == 'SW' else 'MEP'
                                for pour in pour_list:
                                    pour_level = map_pour_to_level(pour)
                                    if pour_level in pour_levels:
                                        open_pour_counts[pour_level][cat] += 1
                                    else:
                                        open_common_counts[cat] += 1
                    
                    # Process tower-specific CommonArea data
                    if re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE):
                        # Resolved data
                        if original_key in resolved_sites:
                            disciplines = resolved_sites[original_key].get("Discipline", [])
                            for discipline in disciplines:
                                cat = 'Civil Finishing' if discipline == 'FW' else 'Structure Works' if discipline == 'SW' else 'MEP'
                                resolved_common_counts[cat] += 1
                        
                        # Open data
                        if original_key in open_sites:
                            disciplines = open_sites[original_key].get("Discipline", [])
                            for discipline in disciplines:
                                cat = 'Civil Finishing' if discipline == 'FW' else 'Structure Works' if discipline == 'SW' else 'MEP'
                                open_common_counts[cat] += 1
                
                # Aggregate pour and CommonArea counts
                for cat in categories:
                    resolved_counts[cat] = sum(resolved_pour_counts[level][cat] for level in pour_levels) + resolved_common_counts[cat]
                    open_counts[cat] = sum(open_pour_counts[level][cat] for level in pour_levels) + open_common_counts[cat]
            
            site_total = sum(resolved_counts.values()) + sum(open_counts.values())
            
            worksheet.write(row, 0, site, tower_total_format)
            for i, display_cat in enumerate(categories):
                worksheet.write(row, i+1, resolved_counts[display_cat], cell_format)
            for i, display_cat in enumerate(categories):
                worksheet.write(row, i+4, open_counts[display_cat], cell_format)
            worksheet.write(row, 7, site_total, cell_format)
            site_totals[site] = site_total
            row += 1
            
            if "Tower" in site and site != "Common_Area":
                for idx, level in enumerate(pour_levels, 1):
                    level_total = sum(resolved_pour_counts[level].values()) + sum(open_pour_counts[level].values())
                    worksheet.write(row, 0, f"Pour {idx}", site_format)
                    for i, display_cat in enumerate(categories):
                        worksheet.write(row, i+1, resolved_pour_counts[level][display_cat], cell_format)
                    for i, display_cat in enumerate(categories):
                        worksheet.write(row, i+4, open_pour_counts[level][display_cat], cell_format)
                    worksheet.write(row, 7, level_total, cell_format)
                    row += 1
                
                common_total = sum(resolved_common_counts.values()) + sum(open_common_counts.values())
                common_desc = "Common Description"
                worksheet.write(row, 0, common_desc, site_format)
                for i, display_cat in enumerate(categories):
                    worksheet.write(row, i+1, resolved_common_counts[display_cat], cell_format)
                for i, display_cat in enumerate(categories):
                    worksheet.write(row, i+4, open_common_counts[display_cat], cell_format)
                worksheet.write(row, 7, common_total, cell_format)
                row += 1

        # Combined NCR Detail Sheets
        def write_detail_sheet(sheet_name, data, title):
            truncated_sheet_name = truncate_sheet_name(f"{sheet_name} {date_part}")
            detail_worksheet = workbook.add_worksheet(truncated_sheet_name)
            detail_worksheet.set_column('A:A', 20)
            detail_worksheet.set_column('B:B', 60)
            detail_worksheet.set_column('C:D', 20)
            detail_worksheet.set_column('E:E', 15)
            detail_worksheet.set_column('F:F', 15)
            detail_worksheet.merge_range('A1:F1', f"{title} {date_part}", title_format)
            headers = ['Site', 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status', 'Discipline']
            for col, header in enumerate(headers):
                detail_worksheet.write(1, col, header, header_format)
            row = 2
            for site, site_data in data.items():
                normalized_sites = site_mapping.get(site, [site])
                for normalized_site in normalized_sites:
                    display_site = normalized_site if normalized_site == "Common_Area" else normalized_site
                    descriptions = site_data.get("Descriptions", [])
                    created_dates = site_data.get("Created Date (WET)", [])
                    close_dates = site_data.get("Expected Close Date (WET)", [])
                    statuses = site_data.get("Status", [])
                    disciplines = site_data.get("Discipline", [])
                    max_length = max(len(descriptions), len(created_dates), len(close_dates), len(statuses), len(disciplines))
                    for i in range(max_length):
                        detail_worksheet.write(row, 0, display_site, default_site_format)
                        detail_worksheet.write(row, 1, descriptions[i] if i < len(descriptions) else "", description_format)
                        detail_worksheet.write(row, 2, created_dates[i] if i < len(created_dates) else "", default_cell_format)
                        detail_worksheet.write(row, 3, close_dates[i] if i < len(close_dates) else "", default_cell_format)
                        detail_worksheet.write(row, 4, statuses[i] if i < len(statuses) else "", default_cell_format)
                        detail_worksheet.write(row, 5, disciplines[i] if i < len(disciplines) else "", default_cell_format)
                        row += 1

        if resolved_sites:
            write_detail_sheet("Closed NCR Details", resolved_sites, "Closed NCR Details")
        if open_sites:
            write_detail_sheet("Open NCR Details", open_sites, "Open NCR Details")

        # 2. Safety and Housekeeping Reports
        def write_safety_housekeeping_report(report_type, data, report_title, sheet_type):
            worksheet = workbook.add_worksheet(truncate_sheet_name(f'{report_type} NCR {sheet_type} {date_part}'))
            worksheet.set_column('A:A', 20)
            worksheet.set_column('B:B', 15)
            worksheet.merge_range('A1:B1', f"{report_title} - {sheet_type}", title_format)
            row = 1
            worksheet.write(row, 0, 'Site', header_format)
            worksheet.write(row, 1, f'No. of {report_type} NCRs beyond 7 days', header_format)
            sites_data = data.get(report_type, {}).get("Sites", {})
            site_mapping_safety = {}
            for k in sites_data.keys():
                normalized = normalize_site_name(k)
                if isinstance(normalized, tuple):
                    for tower in normalized:
                        site_mapping_safety.setdefault(k, []).append(tower)
                else:
                    site_mapping_safety[k] = [normalized]
            
            row = 2
            for site in standard_sites:
                original_keys = [k for k, v in site_mapping_safety.items() if site in v]
                value = 0
                for original_key in original_keys:
                    if not re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE) or site != "Common_Area":
                        value += sites_data.get(original_key, {}).get("Count", 0)
                worksheet.write(row, 0, site, default_site_format)
                worksheet.write(row, 1, value, default_cell_format)
                row += 1
            
            worksheet_details = workbook.add_worksheet(truncate_sheet_name(f'{report_type} NCR {sheet_type} Details {date_part}'))
            worksheet_details.set_column('A:A', 20)
            worksheet_details.set_column('B:B', 60)
            worksheet_details.set_column('C:D', 20)
            worksheet_details.set_column('E:E', 15)
            worksheet_details.set_column('F:F', 15)
            worksheet_details.merge_range('A1:F1', f"{report_title} - {sheet_type} Details", title_format)
            headers = ['Site', 'Description', 'Created Date (WET)', 'Expected Close Date (WET)', 'Status', 'Discipline']
            row = 1
            for col, header in enumerate(headers):
                worksheet_details.write(row, col, header, header_format)
            row = 2
            for site in standard_sites:
                original_keys = [k for k, v in site_mapping_safety.items() if site in v]
                for original_key in original_keys:
                    if not re.search(r'(?:eden-)?tower-\d+(?:-\d+)?-commonarea', original_key, re.IGNORECASE) or site != "Common_Area":
                        site_data = sites_data.get(original_key, {})
                        descriptions = site_data.get("Descriptions", [])
                        created_dates = site_data.get("Created Date (WET)", [])
                        close_dates = site_data.get("Expected Close Date (WET)", [])
                        statuses = site_data.get("Status", [])
                        max_length = max(len(descriptions), len(created_dates), len(close_dates), len(statuses))
                        for i in range(max_length):
                            worksheet_details.write(row, 0, site, default_site_format)
                            worksheet_details.write(row, 1, descriptions[i] if i < len(descriptions) else "", description_format)
                            worksheet_details.write(row, 2, created_dates[i] if i < len(created_dates) else "", default_cell_format)
                            worksheet_details.write(row, 3, close_dates[i] if i < len(close_dates) else "", default_cell_format)
                            worksheet_details.write(row, 4, statuses[i] if i < len(statuses) else "", default_cell_format)
                            worksheet_details.write(row, 5, "HSE", default_cell_format)
                            row += 1

        safety_closed_data = all_reports.get("Safety_NCR_Closed", {})
        report_title_safety = f"Safety NCR: {date_part}"
        write_safety_housekeeping_report("Safety", safety_closed_data, report_title_safety, "Closed")
        safety_open_data = all_reports.get("Safety_NCR_Open", {})
        write_safety_housekeeping_report("Safety", safety_open_data, report_title_safety, "Open")
        housekeeping_closed_data = all_reports.get("Housekeeping_NCR_Closed", {})
        report_title_housekeeping = f"Housekeeping NCR: {date_part}"
        write_safety_housekeeping_report("Housekeeping", housekeeping_closed_data, report_title_housekeeping, "Closed")
        housekeeping_open_data = all_reports.get("Housekeeping_NCR_Open", {})
        write_safety_housekeeping_report("Housekeeping", housekeeping_open_data, report_title_housekeeping, "Open")

    output.seek(0)
    return output

# Streamlit UI

# Helper function to generate report title
def generate_report_title(prefix):
    now = datetime.now()  # Current date: April 25, 2025
    day = now.strftime("%d")
    month_name = now.strftime("%B")
    year = now.strftime("%Y")
    return f"{prefix}: {day}_{month_name}_{year}"

# Generate Safety NCR Report


# Generate Housekeeping NCR Report


# All Reports Button


