import json
import os
import re
from typing import Any, Dict, List

# ==============================================================================
# CMCR Native Case Parser
# Strictly parses structured clinical reports from Chinese Medical Case Repository (CMCR)
# adhering to the native sections defined in CARE guidelines with zero preset departments.
# ==============================================================================

def parse_cmcr_raw_text_to_native_schema(
    raw_text: str,
    cmaid: str,
    title: str,
    doi: str,
    hospital: str,
    dept: str,
    authors: List[str]
) -> Dict[str, Any]:
    """
    Parses native CMCR clinical case text into a 1:1 structured clinical schema
    without hardcoding or presetting any predefined department boundaries.
    """
    schema = {
        "case_metadata": {
            "cmaid": cmaid,
            "doi": doi,
            "title": title,
            "reporting_institution": hospital,
            "reporting_department": dept,
            "authors": authors,
            "database_source": "中国临床案例成果数据库 (CMCR)"
        },
        "clinical_record": {
            "chief_complaint": {},
            "history_of_present_illness": {},
            "past_medical_history": {},
            "physical_examination": {},
            "initial_emergency_management": {},
            "auxiliary_examinations": {},
            "differential_diagnosis": {},
            "final_diagnoses": [],
            "treatment_course": {},
            "outcome_and_followup": {},
            "discussion_and_pearls": {}
        },
        "triage_agent_derived_features": {
            "patient_input_view": {},
            "misleading_biases": [],
            "key_discriminative_slots": [],
            "red_flag_triggers": [],
            "actual_involved_specialties": [],
            "grounded_guidance_text": ""
        }
    }

    # Extract Chief Complaint (主诉)
    cc_match = re.search(r"主诉[：:]\s*(.+?)(?=\n|现病史|体格检查|$)", raw_text)
    if cc_match:
        cc_text = cc_match.group(1).strip()
        schema["clinical_record"]["chief_complaint"]["raw_text"] = cc_text
        dur_match = re.search(r"(\d+\s*[天日d周月年小时h]+)", cc_text)
        schema["clinical_record"]["chief_complaint"]["duration"] = dur_match.group(1) if dur_match else ""

    # Extract Present Illness (现病史)
    hpi_match = re.search(r"现病史[：:]\s*(.+?)(?=\n既往史|体格检查|初步诊疗|$)", raw_text, re.DOTALL)
    if hpi_match:
        schema["clinical_record"]["history_of_present_illness"]["narrative"] = hpi_match.group(1).strip()

    # Extract Physical Exam (体格检查 / 生命体征)
    pe_match = re.search(r"体格检查[：:]\s*(.+?)(?=\n辅助检查|初步诊疗|既往史|$)", raw_text, re.DOTALL)
    if pe_match:
        pe_text = pe_match.group(1).strip()
        schema["clinical_record"]["physical_examination"]["narrative"] = pe_text
        # Parse vitals
        t_m = re.search(r"体温\s*([\d\.]+)\s*℃", pe_text)
        bp_m = re.search(r"血压\s*(\d+)/(\d+)\s*mmHg", pe_text)
        hr_m = re.search(r"心率\s*(\d+)\s*次", pe_text)
        rr_m = re.search(r"呼吸\s*(\d+)\s*次", pe_text)
        spo2_m = re.search(r"(?:血氧饱和度|SpO2)\s*([\d\.]+)%", pe_text)

        schema["clinical_record"]["physical_examination"]["vital_signs"] = {
            "temperature_c": float(t_m.group(1)) if t_m else None,
            "systolic_bp": int(bp_m.group(1)) if bp_m else None,
            "diastolic_bp": int(bp_m.group(2)) if bp_m else None,
            "heart_rate": int(hr_m.group(1)) if hr_m else None,
            "respiratory_rate": int(rr_m.group(1)) if rr_m else None,
            "spo2_percent": float(spo2_m.group(1)) if spo2_m else None
        }

    return schema

if __name__ == "__main__":
    print("CMCR Native Extractor initialized successfully.")