# B-Glow Backend API

This is the backend API for the B-Glow application, built with Python and Flask.

## Features
- **Authentication**: User login and registration (`auth.py`).
- **Skincare Routine**: Management of user skincare routines (`routine.py`).
- **Recommendations**: Product and ingredient recommendation system (`recommend.py`).
- **Ingredients Database**: API to fetch skincare ingredients from dataset.

## Prerequisites

- Python 3.x
- MySQL Database

## Setup and Installation

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone https://github.com/dikaenwo/backend-TA.git
   cd backend-TA
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Windows
   python -m venv venv
   .\venv\Scripts\activate
   
   # macOS/Linux
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Database Configuration**:
   - Ensure MySQL is running.
   - Configure your database credentials in `db.py`.
   - The database will be initialized automatically when running the app based on `init_db.sql`.

## Running the Application

Start the Flask server:

```bash
python app.py
```

The server will run on `http://0.0.0.0:5001` or `http://localhost:5001`.

## API Endpoints Overview
- `GET /` - Check API status
- `GET /api/ingredients` - Get list of ingredients
- `GET /api/ingredients/<slug>` - Get specific ingredient detail
- Plus other endpoints under `/auth`, `/routine`, and `/recommend`.
