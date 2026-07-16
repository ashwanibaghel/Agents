"""
tests/manual_gemini_provider_probe.py — Gemini API Provider Probe
==================================================================
This script performs a minimal capability probe of the Gemini Developer API:
  1. Lists available models using the provided GEMINI_API_KEY.
  2. Identifies a suitable model (preferring gemini-2.5-flash, gemini-2.5-flash-lite, or gemini-1.5-flash).
  3. Sends exactly one harmless coding reasoning request.
  4. Diagnoses rate limit, billing, authentication, or model errors.

No repository files are modified. No more than 2 API calls are made.
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv(override=True)

def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set or is empty.")
        sys.exit(1)

    print("=" * 60)
    print("GEMINI API PROVIDER PROBE")
    print("=" * 60)

    # Step 1: List models
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    
    print("Step 1: Listing available models...")
    try:
        r_list = requests.get(list_url, timeout=15)
        list_status = r_list.status_code
    except Exception as e:
        print(f"ERROR: Failed to connect to Gemini API: {e}")
        sys.exit(1)

    if list_status == 400:
        # Key validation error or bad request
        print("HTTP STATUS: 400")
        print("ERROR_TYPE: BAD_REQUEST (check if API key is malformed or invalid)")
        print(r_list.text)
        sys.exit(1)
    elif list_status in [401, 403]:
        print(f"HTTP STATUS: {list_status}")
        print("ERROR_TYPE: AUTHENTICATION_FAILED (invalid API key or permission denied)")
        sys.exit(1)
    elif list_status == 429:
        print("HTTP STATUS: 429")
        print("ERROR_TYPE: QUOTA_EXCEEDED (Rate limit or quota hit)")
        sys.exit(1)
    elif list_status != 200:
        print(f"HTTP STATUS: {list_status}")
        print(f"ERROR_TYPE: UNEXPECTED_ERROR: {r_list.text}")
        sys.exit(1)

    # Successfully retrieved list of models
    try:
        models_data = r_list.json()
        models = [m.get("name", "") for m in models_data.get("models", [])]
    except Exception as e:
        print(f"ERROR: Failed to parse models JSON response: {e}")
        print(r_list.text)
        sys.exit(1)

    # Look for current standard models
    selected_model = None
    pref_order = [
        "models/gemini-2.5-flash",
        "models/gemini-2.5-flash-lite",
        "models/gemini-1.5-flash",
        "models/gemini-2.0-flash",
    ]
    
    # Try exact matches from preference list first
    for p in pref_order:
        if p in models:
            selected_model = p
            break
            
    # Fallback to any flash model in the list
    if not selected_model:
        for m in models:
            if "flash" in m.lower():
                selected_model = m
                break
                
    # Ultimate fallback if list is empty or unexpected
    if not selected_model:
        if models:
            selected_model = models[0]
        else:
            selected_model = "models/gemini-2.5-flash"  # Hardcoded default guess

    clean_model_name = selected_model.replace("models/", "")
    print(f"Selected model for probe: {clean_model_name}")

    # Step 2: Make exactly one harmless coding reasoning request
    generate_url = f"https://generativelanguage.googleapis.com/v1beta/{selected_model}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{
            "parts": [{
                "text": "Given a Next.js repository, explain in 3 concise points how you would identify its application entry point. Do not modify files."
            }]
        }],
        "generationConfig": {
            "maxOutputTokens": 400,
            "temperature": 0.2
        }
    }
    
    headers = {"Content-Type": "application/json"}
    
    print(f"Step 2: Requesting content generation from model: {clean_model_name}...")
    try:
        r_gen = requests.post(generate_url, headers=headers, json=payload, timeout=20)
        gen_status = r_gen.status_code
        response_text = r_gen.text
    except Exception as e:
        print(f"ERROR: Generation request failed: {e}")
        sys.exit(1)

    # Parse response status and check for specific API errors
    is_success = (gen_status == 200)
    summary_text = ""
    
    if is_success:
        try:
            res_data = r_gen.json()
            # Extract content text safely
            candidates = res_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    summary_text = parts[0].get("text", "").strip()
        except Exception as e:
            summary_text = f"Failed to parse generation JSON: {e}"
            is_success = False
    else:
        # Diagnostic analysis
        try:
            err_data = r_gen.json()
            err_msg = err_data.get("error", {}).get("message", "")
            err_status = err_data.get("error", {}).get("status", "")
            
            summary_text = f"API Error: {err_status} - {err_msg}"
            
            if "quota" in err_msg.lower() or gen_status == 429:
                print("DIAGNOSIS: QUOTA_EXCEEDED (Check rate limits or project usage)")
            elif "billing" in err_msg.lower():
                print("DIAGNOSIS: BILLING_REQUIRED (Paid account or billing setup needed)")
            elif "not found" in err_msg.lower() or gen_status == 404:
                print("DIAGNOSIS: MODEL_NOT_FOUND (Selected model is not available for this key)")
            elif gen_status in [401, 403]:
                print("DIAGNOSIS: AUTHENTICATION_FAILED")
            else:
                print(f"DIAGNOSIS: UNEXPECTED_API_FAILURE ({gen_status})")
        except Exception:
            summary_text = f"API returned non-200 status {gen_status}. Raw output: {response_text[:300]}"

    # Output formatted report
    print("\n" + "=" * 60)
    print(f"MODEL_USED: {clean_model_name}")
    print(f"HTTP/API STATUS: {gen_status}")
    print(f"RESPONSE_RECEIVED: {str(is_success).lower()}")
    print("RESPONSE_SUMMARY:")
    if is_success:
        print(summary_text)
    else:
        print(f"Execution failed: {summary_text}")
    print("=" * 60)

if __name__ == "__main__":
    main()
