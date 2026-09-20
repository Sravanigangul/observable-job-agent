"""Test Jev on one candidate-job decision."""

from typesafe_sdk import Choice, TypeSafeClient
from job_scout.config import get_settings


state = {
    "candidate": {
        "target_role": "Healthcare Data Scientist",
        "experience": "Five years in healthcare industry",
        "skills": [
            "Python",
            "SQL",
            "machine learning",
            "clinical data",
            "Power BI",
            "Epic"
            "AI",
        ],
    },

    "job": {
    "title": "Data Scientist",
    "description": (
        "Work with data using Python and SQL. "
        "Additional experience, education, location, "
        "and employment requirements will be discussed during the interview."
    ),
},
}

questions = {
    "decision": Choice(
        instructions=(
            "Choose the candidate's next action. "
            "Missing or incomplete job information is uncertainty, not a mismatch. "
            "Never choose skip only because information is missing. "
            "Choose skip only when the job explicitly states requirements "
            "that conflict with the candidate's supplied qualifications."
        ),
        criteria={
            "apply": (
                "The available information shows that the candidate meets "
                "most important requirements and the role aligns with the target career."
            ),
            "review": (
                "Use this when important requirements, seniority, location, "
                "education, or employment conditions are missing or unclear. "
                "Also use this for a partial match that requires human judgment."
            ),
            "skip": (
                "Use only when the job explicitly requires qualifications "
                "the candidate clearly lacks, or the role is explicitly unrelated. "
                "Do not use skip merely because the posting is incomplete."
            ),
        },
    )
}

settings = get_settings()

with TypeSafeClient(
    api_key=settings.typesafe_api_key.get_secret_value()
) as client:
    response = client.system_one(
        state=state,
        questions=questions,
    )

decision = response.choices["decision"]

CONFIDENCE_THRESHOLD = 0.50

if decision.confidence < CONFIDENCE_THRESHOLD:
    final_decision = "review"
else:
    final_decision = decision.choice

print("\nJev result")
print("----------")
print("Raw Jev choice:", decision.choice)
print("Confidence:", decision.confidence)
print("Probabilities:", decision.probabilities)
print("Final routed decision:", final_decision)