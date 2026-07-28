import yaml

def load_config(str):
    with open('config.yaml', 'r') as file:
        config = yaml.safe_load(file)
    return config[str]