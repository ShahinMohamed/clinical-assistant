import json
import sys
from collections import Counter
from pathlib import Path


TYPE_2_DIABETES_CODES = {"44054006"}
HBA1C_CODES = {"4548-4"}
BLOOD_PRESSURE_CODES = {
    "85354-9",  # Blood-pressure panel
    "55284-4",  # Blood pressure systolic and diastolic
    "8480-6",   # Systolic blood pressure
    "8462-4",   # Diastolic blood pressure
}


def get_codes(resource):
    """Return codes from a FHIR resource and its components."""
    codes = set()
    concepts = [resource.get("code", {})]

    for component in resource.get("component", []):
        concepts.append(component.get("code", {}))

    for concept in concepts:
        for coding in concept.get("coding", []):
            code = coding.get("code")
            if code:
                codes.add(code)

    return codes


def profile_fhir_directory(directory):
    resource_counts = Counter()
    patient_ids = set()

    markers = {
        "type_2_diabetes": 0,
        "hba1c": 0,
        "blood_pressure": 0,
    }

    files_scanned = 0
    valid_files = 0
    invalid_files = []

    for file_path in sorted(directory.glob("*.json")):
        files_scanned += 1

        try:
            with file_path.open(encoding="utf-8") as file:
                bundle = json.load(file)

            if bundle.get("resourceType") != "Bundle":
                raise ValueError("Root resource is not a FHIR Bundle")

            entries = bundle.get("entry")

            if not isinstance(entries, list):
                raise ValueError("Bundle.entry is not a list")

            for index, entry in enumerate(entries):
                resource = entry.get("resource")

                if not isinstance(resource, dict):
                    raise ValueError(
                        f"Entry {index} does not contain a resource"
                    )

                resource_type = resource.get("resourceType")

                if not resource_type:
                    raise ValueError(
                        f"Entry {index} has no resourceType"
                    )

                resource_counts[resource_type] += 1

                if resource_type == "Patient":
                    patient_id = resource.get("id")

                    if patient_id:
                        patient_ids.add(patient_id)

                if resource_type in {"Condition", "Observation"}:
                    codes = get_codes(resource)

                    if (
                        resource_type == "Condition"
                        and codes & TYPE_2_DIABETES_CODES
                    ):
                        markers["type_2_diabetes"] += 1

                    if (
                        resource_type == "Observation"
                        and codes & HBA1C_CODES
                    ):
                        markers["hba1c"] += 1

                    if (
                        resource_type == "Observation"
                        and codes & BLOOD_PRESSURE_CODES
                    ):
                        markers["blood_pressure"] += 1

            valid_files += 1

        except (json.JSONDecodeError, OSError, ValueError) as error:
            invalid_files.append(
                {
                    "file": file_path.name,
                    "error": str(error),
                }
            )

    required_markers_found = all(
        markers[name] > 0
        for name in (
            "type_2_diabetes",
            "hba1c",
            "blood_pressure",
        )
    )

    return {
        "directory": str(directory.resolve()),
        "files": {
            "scanned": files_scanned,
            "valid": valid_files,
            "invalid": len(invalid_files),
        },
        "patients": len(patient_ids),
        "resources": {
            "total": sum(resource_counts.values()),
            "by_type": dict(resource_counts.most_common()),
        },
        "clinical_markers": markers,
        "validation_errors": invalid_files,
        "phase_2_passed": (
            len(invalid_files) == 0
            and required_markers_found
        ),
    }


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python profile_fhir.py <fhir-directory>",
            file=sys.stderr,
        )
        return 1

    directory = Path(sys.argv[1])

    if not directory.is_dir():
        print(f"Directory not found: {directory}", file=sys.stderr)
        return 1

    report = profile_fhir_directory(directory)
    print(json.dumps(report, indent=2))

    return 0 if report["phase_2_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())