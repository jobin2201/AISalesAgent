# Website Module (Isolated)

This folder contains a separate Bootstrap-based web UI that reuses existing backend functionality from:

- `app/mainapp2_support`
- `app/mainapp2_tabs` (indirectly via shared support logic)
- `app/conversation_flow`

No existing project files are required to be changed to run this module.

## Folder Structure

- `website/app.py` - Flask backend API + page renderer
- `website/templates/index.html` - Main HTML
- `website/static/css/styles.css` - Styles
- `website/static/js/app.js` - Frontend logic

## Features Included

- Sidebar/offcanvas panel
- Clickable notification bell + notification page
- Lead dashboard with filters
- Lead detail and sender conversation preview
- Sender reply plan generation and apply flow
- Customer reply template and draft flow
- Two-way cycle trigger
- Message logs table
- Test notification button

## Run

From `salesagent` folder:

```powershell
pip install -r website/requirements.txt
python website/app.py
```

Open:

- `http://127.0.0.1:5055`

## Notes

- This module imports and uses existing collections/configuration from your current project.
- Notification generation and workflow behavior remain in existing modules; this website acts as a separate UI/controller layer.
