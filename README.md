# Kindred Care

A colorful, accessible telemedicine dashboard prototype designed for older adults. Run the local Python server below; it serves the frontend and persists demo data in SQLite. Python 3.10+ is required and no third-party packages are needed.

## Run locally

From this folder, start the backend:

```powershell
py -3 server.py
```

If the Windows Python launcher is unavailable, use `python server.py`. Then open `http://127.0.0.1:8000` in your browser. Do not open `index.html` directly or use a static file preview: the app needs this local server for its API, and those previews may return a plain-text `Forbidden` response. By default the server binds to this computer only and creates `data/kindred_demo.sqlite3`, which stores appointments, clinician-entered visit reviews/prescriptions, reminder preferences, and daily medication check-offs. Keep the server running while using the app. Stop it with Ctrl+C. The database file stays on disk across restarts.

## Public demo deployment

The included `render.yaml` can be used to deploy the frontend and API together as a free Render web service:

1. Put this project in a GitHub repository (do not upload `.venv` or the `data` folder).
2. In Render, create a new Blueprint and connect that repository. Render will read `render.yaml`; deploy the `kindred-care-demo` service.
3. Open the service URL Render provides. The free service may sleep when idle, so the first visit can take a little longer. Its filesystem is temporary, so saved demo data can be lost when the service restarts or redeploys.

Each set of matching name, date of birth, and mobile number signs in to its own demo profile; entering a different set creates a new profile with separate appointments, reminders, and medication check-offs. This is only a demo profile gate, not a secure account system: the details are not strong credentials, and the public app and unencrypted database are not suitable for personal or medical information. Anyone can create a fictional profile. Use fictional data only; never enter real personal, medical, or login information. The link becomes available only after the service is deployed in a Render account.

## Prototype features

- Appointment booking that stays empty until the required doctor, visit type, date/time, and concern form is submitted; patients can also mention other conditions
- A colorful demo-profile entry screen that saves a name, date of birth, and mobile number through the local backend before opening the app
- Sample video and audio visit screens, including an optional local camera preview
- No medicines or prescriptions appear before a consultation is completed
- After a sample consultation ends, the visit waits for clinician review; a demo clinician workspace filters pending visits to the doctor selected when booking, and that clinician can enter the assessment, follow-up instructions, and multiple prescriptions linked to confirmed conditions
- Prescription details include the linked confirmed condition, medicine type/name, strength/form, numeric amount and unit per dose, clinician-entered doses per day, exact schedule, route, duration, quantity, refills, pharmacy, missed-dose guidance, side-effect/warning information, and other clinician instructions
- Visit and prescription history, linked to each completed sample visit
- Medication check-offs and a local reminder toggle appear only for medicines added after a completed sample visit
- An interactive, scripted AI care guide demo available from the dashboard when a doctor is unavailable; it shares general information and helps prepare clinician questions
- Sample care summary and lab results
- Responsive layout, keyboard-operable dialogs, visible focus indicators, reduced-motion support, and larger controls

## Important limitations

This remains a front-end/backend demonstration, not a healthcare service. The profile screen matches a profile by name, date of birth, and mobile number; new details create a separate profile. These details are saved unencrypted in SQLite and do not verify identity or provide secure authentication. Use fictional details only; do not enter real personal or health information. Appointments, preferences, and medication check-offs are scoped to each demo profile, but the public service and its SQLite data are not encrypted or backed up. No appointment or medicine is displayed before its corresponding forms are completed after the appropriate booking or consultation step. After a sample consultation ends, its appointment is marked as awaiting review; no prescription is added until a clinician is selected in the demo workspace and submits the visit review. The workspace filters visits by the doctor assigned at booking, but the clinician selector is not authentication and does not enforce real doctor-only access. The post-visit form simulates clinician-entered information for demonstration only; it does not assess a patient or issue a valid prescription. Prescription fields, including medicine type and dose amounts/counts, must be copied from an authorized clinician's instructions; the demo does not calculate dosage or validate drug safety, interactions, or suitability. The AI care guide uses prepared on-device replies; it is not a connected AI model or clinician and does not diagnose, assess symptoms, recommend treatment, or prescribe. Camera access only shows a local preview; it does not connect to a clinician or transmit audio/video. The reminder control saves a preference but does not send notifications. Records formerly saved in browser local storage are not imported into the SQLite database.

Before handling patient information, replace this demo server and profile gate with an established identity provider or clinic authentication system and a backend that enforces patient/clinician role-based authorization and verifies the clinician assigned to each appointment, with appropriate encryption, audit logs, backups, retention policies, and healthcare privacy/compliance review. Binding the local server to localhost and filtering by clinician name are not secure access controls. Live consultations also need a secure video/audio provider and signaling service; reminders need a dependable, consent-based notification service; prescribing and records must integrate with authorized clinical systems, and prescriptions must be authored and approved by licensed clinicians.
