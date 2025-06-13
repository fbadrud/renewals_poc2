# Agentic Renewals System with Decision-Making and Human-Readable Output

import logging
import os
import json
import requests
import datetime
import azure.functions as func
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI

# === Shared Agent Registry ===
class AgentRegistry:
    def __init__(self):
        self.agents = {}

    def register(self, name, func):
        self.agents[name] = func

    def call(self, name, data=None):
        try:
            response = requests.get(f"https://renewal-agents-fn.azurewebsites.net/api/{name}")
            return response.text
        except Exception as e:
            return f"Error calling {name}: {str(e)}"

agent_registry = AgentRegistry()

# === Agent: DataPrepAgent ===
@app.function_name(name="DataPrepAgent")
@app.route(route="dataprep", auth_level=func.AuthLevel.FUNCTION)
def dataprep_agent(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("DataPrepAgent triggered.")
    try:
        blob_connection = os.environ["BLOB_CONNECTION_STRING"]
        blob_service_client = BlobServiceClient.from_connection_string(blob_connection)
        blob_data = blob_service_client.get_blob_client(container="data", blob="renewals.json").download_blob().readall()
        data = json.loads(blob_data)

        cleaned = [r for r in data if all(r.get(k) for k in ["customerId", "companyName", "expirationDate"])]
        messages = [f"✅ Record for {r['companyName']} looks good." for r in cleaned]

        return func.HttpResponse("\n".join(messages), status_code=200, mimetype="text/plain")
    except Exception as e:
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)

# === Agent: GapDetectionAgent ===
@app.function_name(name="GapDetectionAgent")
@app.route(route="GapDetectionAgent", auth_level=func.AuthLevel.ANONYMOUS)
def gap_detection_agent(req: func.HttpRequest) -> func.HttpResponse:
    try:
        conn_str = os.getenv("BLOB_CONNECTION_STRING")
        container = os.getenv("BLOB_CONTAINER_NAME", "data")
        blob = os.getenv("BLOB_NAME", "renewals.json")

        blob_data = BlobServiceClient.from_connection_string(conn_str).get_blob_client(container, blob).download_blob().readall()
        records = json.loads(blob_data)

        report = []
        for r in records:
            issues = []
            if not r.get("lastContact"): issues.append("❌ Missing last contact date")
            if not r.get("notes"): issues.append("❌ Missing notes")
            if r.get("expirationDate"):
                exp = datetime.datetime.strptime(r["expirationDate"], "%Y-%m-%d")
                if (exp - datetime.datetime.today()).days < 30:
                    issues.append("⚠️ Renewal expiring soon")
            if issues:
                report.append(f"{r.get('companyName')}:\n" + "\n".join(issues))

        return func.HttpResponse("\n\n".join(report), status_code=200, mimetype="text/plain")
    except Exception as e:
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)

# === Agent: QuoteBuilderAgent ===
@app.function_name(name="QuoteBuilderAgent")
@app.route(route="QuoteBuilderAgent", auth_level=func.AuthLevel.ANONYMOUS)
def quote_builder_agent(req: func.HttpRequest) -> func.HttpResponse:
    try:
        conn_str = os.getenv("BLOB_CONNECTION_STRING")
        container = os.getenv("BLOB_CONTAINER_NAME", "data")
        blob = os.getenv("BLOB_NAME", "renewal_data.json")

        client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version="2023-07-01-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")

        blob_data = BlobServiceClient.from_connection_string(conn_str).get_blob_client(container, blob).download_blob().readall()
        records = json.loads(blob_data)

        results = []
        for record in records:
            customer = record.get("companyName", "Unknown")
            if not record.get("lastContact") or not record.get("notes"):
                results.append(f"⚠️ Cannot generate quote for {customer}. Missing fields. Please run GapDetectionAgent.")
                continue

            prompt = f"""
You are a helpful renewals assistant. Generate a friendly, professional renewal quote:

Customer Name: {customer}
Product: {record.get('product')}
Current Price: {record.get('currentPrice')}
Last Contact: {record.get('lastContact')}
Notes: {record.get('notes')}
"""
            quote = client.chat.completions.create(
                model=deployment,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=150
            ).choices[0].message.content.strip()

            results.append(f"✅ Quote for {customer}:\n{quote}")

        return func.HttpResponse("\n\n---\n\n".join(results), status_code=200, mimetype="text/plain")
    except Exception as e:
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)

# === Agent: UpcomingRenewalsAgent ===
@app.function_name(name="UpcomingRenewalsAgent")
@app.route(route="UpcomingRenewalsAgent", auth_level=func.AuthLevel.ANONYMOUS)
def upcoming_renewals_agent(req: func.HttpRequest) -> func.HttpResponse:
    try:
        conn_str = os.getenv("BLOB_CONNECTION_STRING")
        container = os.getenv("BLOB_CONTAINER_NAME")
        blob = os.getenv("BLOB_NAME")

        blob_data = BlobServiceClient.from_connection_string(conn_str).get_blob_client(container, blob).download_blob().readall()
        records = json.loads(blob_data)

        today = datetime.date.today()
        upcoming = []
        for r in records:
            try:
                exp = datetime.datetime.strptime(r["expirationDate"], "%Y-%m-%d").date()
                if today <= exp <= today + datetime.timedelta(days=30):
                    upcoming.append(f"🔔 {r['companyName']} — Renewal due on {r['expirationDate']}")
            except:
                continue

        return func.HttpResponse("\n".join(upcoming), status_code=200, mimetype="text/plain")
    except Exception as e:
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)

# === ChatRouterAgent with Delegation ===
@app.function_name(name="ChatRouterAgent")
@app.route(route="ChatRouterAgent", auth_level=func.AuthLevel.ANONYMOUS)
def chat_router_agent(req: func.HttpRequest) -> func.HttpResponse:
    try:
        user_message = req.get_json().get("message", "")

        client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version="2023-07-01-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")

        prompt = f"""
A user says: "{user_message}"
Which of these agents should handle it?
1. QuoteBuilderAgent
2. GapDetectionAgent
3. DataPrepAgent
4. UpcomingRenewalsAgent
Respond ONLY with JSON:
{{"agent": "<name>", "finalAnswer": "<natural language summary>"}}
"""
        choice = json.loads(client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300
        ).choices[0].message.content.strip())

        agent = choice.get("agent")
        summary = choice.get("finalAnswer")
        result = agent_registry.call(agent)

        return func.HttpResponse(f"{summary}\n\n{result}", status_code=200, mimetype="text/plain")
    except Exception as e:
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)

# Register all agents for orchestration use
agent_registry.register("QuoteBuilderAgent", quote_builder_agent)
agent_registry.register("GapDetectionAgent", gap_detection_agent)
agent_registry.register("DataPrepAgent", dataprep_agent)
agent_registry.register("UpcomingRenewalsAgent", upcoming_renewals_agent)
