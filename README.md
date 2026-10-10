# Employee Attendance & Analytics API

A REST API built using FastAPI and MongoDB for employee management, attendance tracking, attendance corrections, and analytics.

## Features

- Create and list employees
- Record employee punch-in and punch-out
- Retrieve attendance records with filters and pagination
- Regularize attendance records
- Generate monthly employee attendance summaries
- Generate department attendance summaries
- View late-arrival leaderboards
- Analyze department attendance trends
- Health-check endpoint

## Tech Stack

- Python
- FastAPI
- Uvicorn
- MongoDB
- PyMongo
- Pydantic
- python-dotenv

## Project Structure

```text
candidate_kit/
├── app/
│   └── main.py
├── data/
├── requirements.txt
├── README.md
├── REVIEW.md
├── DECISIONS.md
├── openapi.yaml
└── DATA_MODEL.md
```

## Prerequisites

- Python 3.10 or later
- MongoDB running locally or a MongoDB connection URI
- pip

## Installation

Clone the repository and enter the project directory:

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd <YOUR_PROJECT_FOLDER>
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Create a local `.env` file in the project root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Use your own MongoDB connection settings when required. Do not commit `.env` or credentials to GitHub.

## Run the API

Start the application from the project root:

```bash
uvicorn app.main:app --port 8000
```

Open the interactive API documentation:

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

If port 8000 is unavailable locally, use another port, such as 8010.

## API Endpoints

The API provides endpoints for:

- Health checks
- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance listing and regularization
- Monthly employee analytics
- Department summaries and trends
- Late-arrival leaderboard
- Query execution-plan inspection

Refer to `openapi.yaml` for the exact paths, request formats, parameters, and response codes.

## Data and Business Rules

- Employees are identified by `emp_code`.
- Attendance records are identified by `emp_code` and attendance date.
- API timestamps use epoch milliseconds.
- Attendance calendar dates follow Indian Standard Time (IST).
- Attendance calculations are derived from punch times and employee shifts.
- Attendance corrections must preserve the correction history.

See `DATA_MODEL.md` for the MongoDB schema and `DECISIONS.md` for implementation decisions.

## Testing

Run the API and use Swagger UI to test the endpoints. Verify successful responses as well as validation errors, duplicate requests, attendance calculations, analytics, and correction history.

## Review Notes

See `REVIEW.md` for issues identified during code review and areas requiring further testing.
