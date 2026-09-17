import os

# Compatibility launcher: always run the current clean AI server from start.py.
# This keeps older Render configurations using `python launcher.py` working too.
path = os.path.join(os.path.dirname(__file__), 'start.py')
source = open(path, encoding='utf-8').read()
exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
