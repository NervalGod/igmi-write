import ollama
from helper import load_config

def request(prompt: str, model_name: str = None) -> str:

    try:
        response = ollama.chat(
            model=load_config('model'),
            messages=[
                    {
                    "role": "user",
                    "content": prompt
                    }
            ]
        )
        return response["message"]["content"].strip()
    except Exception as e:
        return f"Ошибка LLM: {str(e)}"