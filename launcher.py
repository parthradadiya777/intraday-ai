import os

# Compatibility launcher for Render: always execute the latest clean AI UI in start.py.
# Deployment trigger: CLEAN_AI_UI_V2
path = os.path.join(os.path.dirname(__file__), 'start.py')
source = open(path, encoding='utf-8').read()
exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
