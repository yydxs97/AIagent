import os

from dotenv import load_dotenv
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY"),
openai_base_url = os.getenv("OPENAI_BASE_URL"),
openai_model_name = os.getenv("OPENAI_MODEL_NAME")

print(openai_api_key)
print(openai_base_url)
print(openai_model_name)