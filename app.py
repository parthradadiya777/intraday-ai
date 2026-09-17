# Compatibility entrypoint for deployments that still run `python app.py`.
# Always launch the full scanner/server instead of the legacy UI.
import runpy

runpy.run_module('server', run_name='__main__')
