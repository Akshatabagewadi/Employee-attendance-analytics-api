"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="Status must be PRESENT, WFH, or ON_DUTY"
        )

    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds"
            )
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    ts_ist = ts.astimezone(IST)
    date_str = ts_ist.date().isoformat()

    if db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "date": date_str}
    ):
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this date"
        )

    punch_in_utc = ts.astimezone(timezone.utc)
    late_minutes = compute_late_minutes(ts_ist, emp["shift_start"])

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": punch_in_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except Exception:
        raise HTTPException(
            status_code=409,
            detail="Could not create attendance; check for a duplicate record"
        )

    doc.pop("_id", None)
    doc["id"] = str(result.inserted_id)
    doc["punch_in"] = int(ts.timestamp() * 1000)
    return doc




@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page must be >= 1 and page_size must be between 1 and 100"
        )

    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "date_from must be YYYY-MM-DD")

    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "date_to must be YYYY-MM-DD")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    if status and status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(422, "Invalid attendance status")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    cursor = (
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = []
    for doc in cursor:
        for field in ("punch_in", "punch_out"):
            value = doc.get(field)
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                doc[field] = int(value.timestamp() * 1000)
        items.append(doc)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-out")
def punch_out(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds"
            )
        ts = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        )
    else:
        ts = datetime.now(timezone.utc)

    ts_ist = ts.astimezone(IST)
    date_str = ts_ist.date().isoformat()

    record = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": date_str
    })

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found"
        )

    if record.get("punch_in") is None:
        raise HTTPException(
            status_code=422,
            detail="Cannot punch out without punch-in"
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Already punched out"
        )

    punch_in_time = record["punch_in"]
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    if ts <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in"
        )

    if (ts - punch_in_time).total_seconds() > 24 * 3600:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be within 24 hours of punch-in"
        )

    punch_in_ist = punch_in_time.astimezone(IST)
    shift_start_h, shift_start_m = map(
        int, emp["shift_start"].split(":")
    )
    shift_end_h, shift_end_m = map(
        int, emp["shift_end"].split(":")
    )

    shift_start = punch_in_ist.replace(
        hour=shift_start_h, minute=shift_start_m,
        second=0, microsecond=0
    )
    shift_end = punch_in_ist.replace(
        hour=shift_end_h, minute=shift_end_m,
        second=0, microsecond=0
    )

    if shift_end <= shift_start:
        shift_end += timedelta(days=1)

    late_minutes = max(
        0, int((punch_in_ist - shift_start).total_seconds() // 60)
    )
    if late_minutes <= 10:
        late_minutes = 0

    work_hours = round(
        (ts - punch_in_time).total_seconds() / 3600, 2
    )
    overtime_minutes = max(
        0, int((ts.astimezone(IST) - shift_end).total_seconds() // 60)
    )
    half_day = work_hours < 4

    db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "late_minutes": late_minutes,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day,
        }}
    )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    return updated

class RegularizeRequest(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None


def epoch_ms_to_datetime(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 100000000000
    ):
        raise HTTPException(422, "Timestamp must be epoch milliseconds")

    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(422, "Timestamp is out of range")


def serialize_attendance(doc):
    doc.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        value = doc.get(field)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            doc[field] = int(value.timestamp() * 1000)

    return doc


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest
):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })
    if not record:
        raise HTTPException(404, "Attendance record not found")

    changes = {}
    updates = {}

    if body.status is not None:
        if body.status not in {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }:
            raise HTTPException(422, "Invalid attendance status")
        if body.status != record.get("status"):
            changes["status"] = {
                "old": record.get("status"),
                "new": body.status
            }
            updates["status"] = body.status

    for field in ("punch_in", "punch_out"):
        value = getattr(body, field)
        if value is not None:
            converted = epoch_ms_to_datetime(value)
            old = record.get(field)
            if old != converted:
                changes[field] = {
                    "old": int(old.replace(tzinfo=timezone.utc).timestamp() * 1000)
                    if isinstance(old, datetime) and old.tzinfo is None
                    else int(old.timestamp() * 1000)
                    if isinstance(old, datetime)
                    else old,
                    "new": value
                }
                updates[field] = converted

    final_status = updates.get("status", record.get("status"))
    final_in = updates.get("punch_in", record.get("punch_in"))
    final_out = updates.get("punch_out", record.get("punch_out"))

    if final_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                422, "ABSENT or LEAVE cannot include punch times"
            )
        if final_in is not None or final_out is not None:
            changes["punch_in"] = {
                "old": int(final_in.timestamp() * 1000)
                if final_in else None,
                "new": None
            }
            changes["punch_out"] = {
                "old": int(final_out.timestamp() * 1000)
                if final_out else None,
                "new": None
            }
            updates["punch_in"] = None
            updates["punch_out"] = None
            final_in = final_out = None

    elif final_in is None:
        raise HTTPException(
            422, "Presence statuses require punch_in"
        )

    if final_in is not None:
        in_ist = final_in.replace(tzinfo=timezone.utc).astimezone(IST)
        if in_ist.date().isoformat() != date:
            raise HTTPException(
                422, "punch_in must fall on the attendance date in IST"
            )

    if final_out is not None:
        if final_in is None or final_out <= final_in:
            raise HTTPException(
                422, "punch_out must be after punch_in"
            )
        if (final_out - final_in).total_seconds() > 86400:
            raise HTTPException(
                422, "punch_out must be within 24 hours of punch_in"
            )

    if not changes:
        raise HTTPException(422, "Request does not change anything")

    late_minutes = 0
    work_hours = None
    overtime_minutes = 0
    half_day = False

    if final_in is not None:
        in_ist = final_in.replace(tzinfo=timezone.utc).astimezone(IST)
        sh, sm = map(int, emp["shift_start"].split(":"))
        eh, em = map(int, emp["shift_end"].split(":"))
        shift_start = in_ist.replace(
            hour=sh, minute=sm, second=0, microsecond=0
        )
        shift_end = in_ist.replace(
            hour=eh, minute=em, second=0, microsecond=0
        )
        if shift_end <= shift_start:
            shift_end += timedelta(days=1)

        late_minutes = max(
            0, int((in_ist - shift_start).total_seconds() // 60)
        )
        if late_minutes <= 10:
            late_minutes = 0

        if final_out is not None:
            work_hours = round(
                (final_out - final_in).total_seconds() / 3600, 2
            )
            out_ist = final_out.replace(
                tzinfo=timezone.utc
            ).astimezone(IST)
            overtime_minutes = max(
                0, int((out_ist - shift_end).total_seconds() // 60)
            )
            half_day = work_hours < 4

    updates.update({
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    })

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    history_entry = {
        "at": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc),
        "changes": changes,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "history": record.get("history", []),
        },
        {
            "$set": updates,
            "$push": {"history": history_entry}
        }
    )

    if result.modified_count != 1:
        raise HTTPException(409, "Attendance changed concurrently; retry")

    updated = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })

    if updated.get("history"):
        for entry in updated["history"]:
            if isinstance(entry.get("at"), datetime):
                at = entry["at"]
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                entry["at"] = int(at.timestamp() * 1000)

            for change in entry.get("changes", {}).values():
                for side in ("old", "new"):
                    val = change.get(side)
                    if isinstance(val, datetime):
                        if val.tzinfo is None:
                            val = val.replace(tzinfo=timezone.utc)
                        change[side] = int(val.timestamp() * 1000)

    return serialize_attendance(updated)

from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP


def round_half_up(value, places=2):
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum, rounding=ROUND_HALF_UP
        )
    )


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)

    joined_on = datetime.strptime(emp["joined_on"], "%Y-%m-%d").date()

    working_days = sum(
        1 for day in range(1, last_day + 1)
        if month_start.replace(day=day).weekday() < 5
        and month_start.replace(day=day) >= joined_on
    )

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat()
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [
                                {"$eq": ["$half_day", True]},
                                0.5, 1
                            ]},
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]}, 1, 0
                        ]
                    }
                }
            }
        }
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    stats = result[0] if result else {}

    present_days = stats.get("present_days", 0)
    attendance_pct = (
        round_half_up(present_days / working_days * 100)
        if working_days else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")

    start = month_start.isoformat()
    end = month_start.replace(
        day=monthrange(month_start.year, month_start.month)[1]
    ).isoformat()

    employee_match = {}
    if department:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code", "joined": "$joined_on"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", start]},
                                    {"$lte": ["$date", end]},
                                    {"$lte": ["$$joined", "$date"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {
                    "$sum": {
                        "$cond": [
                            {"$lte": ["$joined_on", end]}, 1, 0
                        ]
                    }
                },
                "logs": {"$push": "$logs"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "logs": {
                    "$reduce": {
                        "input": "$logs",
                        "initialValue": [],
                        "in": {"$concatArrays": ["$$value", "$$this"]}
                    }
                }
            }
        },
        {"$match": {"headcount": {"$gt": 0}}},
        {
            "$project": {
                "department": 1,
                "headcount": 1,
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$in": [
                                        "$$log.status",
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]},
                                    {"$cond": [
                                        {"$eq": ["$$log.half_day", True]},
                                        0.5, 1
                                    ]},
                                    0
                                ]
                            }
                        }
                    }
                },
                "avg_work_hours": {
                    "$avg": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$and": [
                                            {"$in": [
                                                "$$log.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]},
                                            {"$ne": ["$$log.work_hours", None]}
                                        ]
                                    }
                                }
                            },
                            "as": "log",
                            "in": "$$log.work_hours"
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$gt": ["$$log.late_minutes", 0]}
                        }
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {"$ifNull": ["$$log.late_minutes", 0]}
                        }
                    }
                },
                "leave_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "LEAVE"]}
                        }
                    }
                },
                "on_duty_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {"$eq": ["$$log.status", "ON_DUTY"]}
                        }
                    }
                }
            }
        },
        {"$sort": {"department": 1}}
    ]

    results = list(db.employees.aggregate(pipeline))

    for item in results:
        if item.get("avg_work_hours") is not None:
            item["avg_work_hours"] = round_half_up(
                item["avg_work_hours"], 2
            )

    return {"items": results}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None
):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")

    if not 1 <= limit <= 50:
        raise HTTPException(422, "limit must be between 1 and 50")

    start = month_start.isoformat()
    end = month_start.replace(
        day=monthrange(month_start.year, month_start.month)[1]
    ).isoformat()

    pipeline = [
        {"$match": {
            "date": {"$gte": start, "$lte": end},
            "late_minutes": {"$gt": 0}
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "employee"
        }},
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {"$sort": {
            "total_late_minutes": -1,
            "_id": 1
        }},
        {"$setWindowFields": {
            "sortBy": {
                "total_late_minutes": -1,
                "_id": 1
            },
            "output": {
                "rank": {"$rank": {}}
            }
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {
            "_id": 0,
            "emp_code": "$_id",
            "name": "$employee.name",
            "department": "$employee.department",
            "total_late_minutes": 1,
            "rank": 1
        }}
    ])

    items = list(db.attendance_logs.aggregate(pipeline))
    return {"items": items}

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(default=None, alias="from"),
    to: str = None,
):
    # The OpenAPI contract uses query parameters named "from" and "to".
    # "from" is a Python keyword, so read it through an alias.
    from_date = from_date
    if not from_date or not to:
        raise HTTPException(422, "Both from and to dates are required")

    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to, "%Y-%m-%d").date()
        if start.isoformat() != from_date or end.isoformat() != to:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Dates must use YYYY-MM-DD")

    days = (end - start).days + 1
    if days <= 0 or days > 92:
        raise HTTPException(
            422, "to must be on or after from, with a maximum range of 92 days"
        )

    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "Department not found")

    pipeline = [
        {"$match": {
            "department": department,
            "joined_on": {"$lte": end.isoformat()}
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$emp_code", "$$code"]},
                            {"$gte": ["$date", start.isoformat()]},
                            {"$lte": ["$date", end.isoformat()]}
                        ]
                    }
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": "$logs"},
        {"$group": {
            "_id": "$logs.date",
            "present_count": {"$sum": {
                "$cond": [
                    {"$in": [
                        "$logs.status", ["PRESENT", "WFH", "ON_DUTY"]
                    ]},
                    {"$cond": [
                        {"$eq": ["$logs.half_day", True]}, 0.5, 1
                    ]},
                    0
                ]
            }},
            "late_count": {"$sum": {
                "$cond": [
                    {"$gt": ["$logs.late_minutes", 0]}, 1, 0
                ]
            }}
        }},
        {"$project": {
            "_id": 0,
            "date": "$_id",
            "present_count": 1,
            "late_count": 1
        }},
        {"$sort": {"date": 1}}
    ]

    # Fill missing calendar dates and calculate headcount/rates.
    daily = {
        item["date"]: item
        for item in db.employees.aggregate(pipeline)
    }

    employees = list(db.employees.find(
        {
            "department": department,
            "joined_on": {"$lte": end.isoformat()}
        },
        {"_id": 0, "joined_on": 1}
    ))

    rows = []
    rates = []

    for offset in range(days):
        day = start + timedelta(days=offset)
        date_str = day.isoformat()
        headcount = sum(
            1 for employee in employees
            if employee["joined_on"] <= date_str
        )
        data = daily.get(date_str, {})
        present_count = data.get("present_count", 0)
        late_count = data.get("late_count", 0)
        working = day.weekday() < 5

        rate = (
            round_half_up(present_count / headcount, 4)
            if working and headcount > 0
            else None
        )
        rates.append(rate)

        window = [value for value in rates[-7:] if value is not None]
        moving_avg = (
            round_half_up(sum(window) / len(window), 4)
            if window else None
        )

        rows.append({
            "date": date_str,
            "is_working_day": working,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": late_count,
            "attendance_rate": rate,
            "moving_avg_7d": moving_avg
        })

    return {"department": department, "from": from_date, "to": to, "items": rows}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    date_from: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    limit: int = 10,
):
    if endpoint not in {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }:
        raise HTTPException(422, "Unknown endpoint")

    if endpoint == "attendance_list":
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if status:
            q["status"] = status
        if date_to or date_from:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

        return db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": max(0, (page - 1) * page_size),
                "limit": page_size,
            },
            "verbosity": "executionStats",
        })

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM")
        end = start.replace(
            day=monthrange(start.year, start.month)[1]
        )
        query = {
            "emp_code": emp_code,
            "date": {"$gte": start.isoformat(), "$lte": end.isoformat()}
        }
        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": [{"$match": query}, {"$group": {
                    "_id": None, "count": {"$sum": 1}
                }}],
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM")
        end = start.replace(
            day=monthrange(start.year, start.month)[1]
        )
        match = {}
        if department:
            match["department"] = department
        return db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": [
                    {"$match": match},
                    {"$lookup": {
                        "from": "attendance_logs",
                        "localField": "emp_code",
                        "foreignField": "emp_code",
                        "as": "logs"
                    }},
                ],
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM")
        end = start.replace(
            day=monthrange(start.year, start.month)[1]
        )
        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {"$match": {
                        "date": {
                            "$gte": start.isoformat(),
                            "$lte": end.isoformat()
                        },
                        "late_minutes": {"$gt": 0}
                    }},
                    {"$group": {
                        "_id": "$emp_code",
                        "total_late_minutes": {"$sum": "$late_minutes"}
                    }},
                ],
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

    if endpoint == "department_trend":
        if not department or not date_from or not to:
            raise HTTPException(
                422, "department, from and to are required"
            )
        return db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": [{"$match": {
                    "date": {"$gte": date_from, "$lte": to}
                }}],
                "cursor": {},
            },
            "verbosity": "executionStats",
        })


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml)uvicorn app.main:app --port 8010S:
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
