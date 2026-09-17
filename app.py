# Compatibility entrypoint for deployments that still run `python app.py`.
# Use the full scanner/server so old Render start commands cannot fall back to the old UI.
import server
