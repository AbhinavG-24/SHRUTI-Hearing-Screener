"""Central configuration for the SHRUTI simulator and console.

BRAND_NAME is the only place the on-screen product name is defined; the HTML
front-end uses the placeholder token ``__BRAND__`` which the server replaces
when it serves the page.
"""

BRAND_NAME = "DR ABHINAV"
APP_VERSION = "0.2.0-prototype"
PROTOCOL_VERSION = "SHRUTI-SIM-v0.2"

SIMULATION_NOTICE = (
    "Simulation: the ear, probe and noise are synthetic models. Nothing shown here is a "
    "measurement of a real device or infant, and results are screening outcomes, not diagnoses."
)
