import json
import os

from google import genai


def get_genai_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured.")
    return genai.Client(api_key=api_key)


def strip_code_fences(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```html"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def parse_transaction_with_gemini(natural_language_text):
    """
    Sends natural language text to Gemini and expects a strict JSON response
    that can be parsed directly into our Django models.
    """
    prompt = f"""
    You are a professional financial assistant.
    Analyze the following transaction text and extract the structured data.

    You MUST respond ONLY with a valid JSON object. No markdown, no explanations, no text outside the JSON.

    Format required:
    {{
        "type": "income" | "expense" | "asset" | "liability",
        "category_name": "string (e.g., 'Revenue', 'Expenses', 'Assets', 'Liabilities')",
        "account_name": "string (e.g., 'Cash', 'Electricity', 'Sales', 'Accounts Receivable')",
        "amount": number,
        "description": "Short summary of the transaction"
    }}

    Transaction Text: "{natural_language_text}"
    """

    client = get_genai_client()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    try:
        return json.loads(strip_code_fences(response.text))
    except Exception as e:
        print(f"[AI Services] Error parsing Gemini response: {e}")
        print(f"[AI Services] Raw Response was: {response.text}")
        return None


def generate_ai_report(question, context_data):
    """
    Asks Gemini to answer the user's question from an orchestrated finance payload.
    """
    serialized_context = json.dumps(context_data, indent=2, default=str)
    prompt = f"""
    You are an expert Financial Analyst.
    You must answer the user's question based strictly on the provided orchestration payload.
    Do not invent data. If the payload does not contain the answer, say so clearly.

    The orchestration payload already contains:
    - detected report intent
    - the reporting period
    - aggregates computed from the database
    - selected transactions when they help explain the answer

    Prefer the provided aggregates over manual recalculation from the transaction list.

    Format your response in clean HTML using elements such as <h3>, <p>, <ul>, <li>, <strong>, and <table> when useful.
    Do not wrap the HTML in markdown code blocks.
    Keep the answer neat, concise, and professional.
    If the requested period has no data, say that plainly and suggest a broader time range.

    User's Question: "{question}"

    Orchestration Payload (JSON):
    {serialized_context}
    """

    client = get_genai_client()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    try:
        return strip_code_fences(response.text)
    except Exception as e:
        print(f"[AI Services] Error generating AI report: {e}")
        return "<p style='color: #ef4444;'>Sorry, an error occurred while generating the report.</p>"
