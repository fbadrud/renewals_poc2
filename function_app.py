import logging
import azure.functions as func
import os
import json
from azure.storage.blob import BlobServiceClient
from datetime import datetime

app = func.FunctionApp()

@app.function_name(name="DataPrepAgent")
@app.route(route="dataprep", auth_level=func.AuthLevel.FUNCTION)
def dataprep_agent(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("DataPrepAgent triggered.")

    try:
        blob_connection = os.environ["BLOB_CONNECTION_STRING"]
        blob_service_client = BlobServiceClient.from_connection_string(blob_connection)

        container_name = "data"
        blob_name = "renewals.json"

        logging.info(f"using container: {container_name}")
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        blob_data = blob_client.download_blob().readall()
        data = json.loads(blob_data)



        # Clean data: Keep only valid records
        cleaned_data = []
        for entry in data:
            if all(k in entry and entry[k] for k in ["customerId", "companyName", "expirationDate"]):
                try:
                    # Optional: Validate expirationDate is a real date
                    datetime.strptime(entry["expirationDate"], "%Y-%m-%d")
                    cleaned_data.append(entry)
                except ValueError:
                    logging.warning(f"Skipping entry with invalid date: {entry}")
            else:
                logging.warning(f"Skipping incomplete entry: {entry}")

        return func.HttpResponse(
            json.dumps(cleaned_data, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Error: {e}")
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)



@app.function_name(name="GapDetectionAgent")
@app.route(route="GapDetectionAgent", auth_level=func.AuthLevel.ANONYMOUS)
def gap_detection_agent(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("GapDetectionAgent triggered.")

    try:
        import os
        from azure.storage.blob import BlobServiceClient
        from datetime import datetime
        import json

        conn_str = os.getenv("BLOB_CONNECTION_STRING")
        container_name = os.getenv("BLOB_CONTAINER_NAME", "data")
        blob_name = os.getenv("BLOB_NAME", "renewals.json")

        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)

        blob_data = blob_client.download_blob().readall()
        records = json.loads(blob_data)

        gaps = []
        for r in records:
            issues = []
            if not r.get("lastContact"):
                issues.append("Missing last contact date")
            if not r.get("notes"):
                issues.append("Missing notes")
            if r.get("expirationDate"):
                exp = datetime.strptime(r["expirationDate"], "%Y-%m-%d")
                if (exp - datetime.today()).days < 30:
                    issues.append("Renewal expiring soon")

            if issues:
                gaps.append({
                    "customerId": r.get("customerId"),
                    "companyName": r.get("companyName"),
                    "issues": issues
                })

        return func.HttpResponse(
            json.dumps(gaps, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"GapDetectionAgent error: {str(e)}")
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)



import logging
import azure.functions as func
import os
import json
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI

@app.function_name(name="QuoteBuilderAgent")
@app.route(route="QuoteBuilderAgent", auth_level=func.AuthLevel.ANONYMOUS)
def quote_builder_agent(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("QuoteBuilderAgent with LLM triggered.")

    try:
        # Load blob env vars
        conn_str = os.getenv("BLOB_CONNECTION_STRING")
        container_name = os.getenv("BLOB_CONTAINER_NAME", "data")
        blob_name = os.getenv("BLOB_NAME", "renewal_data.json")

        # Load OpenAI env vars
        client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version="2023-07-01-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")

        # Load renewal data from Blob Storage
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        blob_client = blob_service_client.get_blob_client(container_name, blob_name)
        blob_data = blob_client.download_blob().readall()
        records = json.loads(blob_data)

        results = []

        for record in records:
            prompt = f"""
You are a helpful renewals assistant. Generate a short, personalized quote renewal message for this customer based on the following:

Customer Name: {record.get('companyName')}
Product: {record.get('product')}
Current Price: {record.get('currentPrice')}
Last Contact Date: {record.get('lastContact')}
Sales Rep Notes: {record.get('notes')}

Respond with a professional but friendly quote message.
"""

            response = client.chat.completions.create(
                model=deployment_name,  # This is your deployment name, not model name
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=150
            )

            quote_text = response.choices[0].message.content.strip()

            results.append({
                "customerId": record.get("customerId"),
                "companyName": record.get("companyName"),
                "quoteMessage": quote_text
            })

        return func.HttpResponse(
            json.dumps(results, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"QuoteBuilderAgent LLM error: {str(e)}")
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)

from openai import AzureOpenAI
import logging
import os
import json
import requests
import azure.functions as func

@app.function_name(name="ChatRouterAgent")
@app.route(route="ChatRouterAgent", auth_level=func.AuthLevel.ANONYMOUS)
def chat_router_agent(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("ChatRouterAgent (LLM-driven) triggered.")

    try:
        user_message = req.get_json().get("message", "")

        # === LLM Setup ===
        client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version="2023-07-01-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        model = os.getenv("AZURE_OPENAI_DEPLOYMENT")

        # === Step 1: Ask GPT what to do ===
        routing_prompt = f"""
You are a backend routing assistant for an AI-powered renewals system.

A user sends the message:
"{user_message}"

Your job is to decide which of these internal agents to call and with what parameters:

Available agents:
1. QuoteBuilderAgent → returns all renewal quotes
2. GapDetectionAgent → returns all records with gaps (e.g., missing notes, no contact)
3. DataPrepAgent → returns raw cleaned renewal data

Respond ONLY with a valid JSON like this:
{{
  "agent": "QuoteBuilderAgent",
  "filter": {{
    "customerId": "CUST002"
  }},
  "finalAnswer": "Getting the renewal quote for customer CUST002..."
}}

If no agent applies, say:
{{ "agent": "none", "finalAnswer": "Sorry, I can't help with that yet." }}
"""

        routing_response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": routing_prompt}],
            temperature=0,
            max_tokens=300
        )

        parsed = json.loads(routing_response.choices[0].message.content)
        agent = parsed.get("agent")
        filter_params = parsed.get("filter", {})
        final_answer = parsed.get("finalAnswer")

        # === Step 2: Call the chosen agent ===
        if agent == "QuoteBuilderAgent":
            r = requests.get("https://renewal-agents-fn.azurewebsites.net/api/QuoteBuilderAgent?")
            results = json.loads(r.text)
            if "customerId" in filter_params:
                results = [r for r in results if r.get("customerId") == filter_params["customerId"]]
            return func.HttpResponse(f"{final_answer}\n\n{json.dumps(results, indent=2)}")

        elif agent == "GapDetectionAgent":
            r = requests.get("https://renewal-agents-fn.azurewebsites.net/api/GapDetectionAgent?")
            return func.HttpResponse(f"{final_answer}\n\n{r.text}")

        elif agent == "DataPrepAgent":
            r = requests.get("https://renewal-agents-fn.azurewebsites.net/api/dataprep?code=jR4hRdZItpRdIbtBJKDJ4iOZmbX7Aq_L3SuzRr5jjPt1AzFuSGlRqQ==")
            return func.HttpResponse(f"{final_answer}\n\n{r.text}")

        else:
            return func.HttpResponse(final_answer, status_code=200)

    except Exception as e:
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)

