# llm.py

import ollama

def request(prompt: str, model_name: str = "llama3.1:8b") -> str:

    try:
        response = ollama.chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}]
        )
        return response["message"]["content"].strip()
    except Exception as e:
        return f"Ошибка LLM: {str(e)}"