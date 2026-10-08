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


def get_codes(resource, system=None):
    """Return codes from a FHIR resource and its components."""
    codes = set()
    concepts = [resource.get("code", {})]

    components = resource.get("component", [])
    if not isinstance(components, list):
        raise ValueError("Resource.component is not a list")
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("Observation component is not an object")
        concepts.append(component.get("code", {}))

    for concept in concepts:
        if not isinstance(concept, dict):
            raise ValueError("Resource code is not a CodeableConcept object")
        codings = concept.get("coding", [])
        if not isinstance(codings, list):
            raise ValueError("CodeableConcept.coding is not a list")
        for coding in codings:
            if not isinstance(coding, dict):
                raise ValueError("Coding is not an object")
            code = coding.get("code")
            if isinstance(code, str) and (system is None or coding.get("system") == system):
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

    for file_path in sorted(directory.rglob("*.json")):
        files_scanned += 1

        try:
            with file_path.open(encoding="utf-8") as file:
                bundle = json.load(file)

            if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
                raise ValueError("Root resource is not a FHIR Bundle")

            entries = bundle.get("entry")

            if not isinstance(entries, list):
                raise ValueError("Bundle.entry is not a list")

            file_counts = Counter()
            file_patients = set()
            file_markers = Counter()
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ValueError(f"Entry {index} is not an object")
                resource = entry.get("resource")

                if not isinstance(resource, dict):
                    raise ValueError(
                        f"Entry {index} does not contain a resource"
                    )

                resource_type = resource.get("resourceType")

                if not isinstance(resource_type, str) or not resource_type:
                    raise ValueError(
                        f"Entry {index} has no resourceType"
                    )

                file_counts[resource_type] += 1

                if resource_type == "Patient":
                    patient_id = resource.get("id")

                    if not isinstance(patient_id, str) or not patient_id:
                        raise ValueError(f"Entry {index} has no usable Patient.id")
                    file_patients.add(patient_id)

                if resource_type in {"Condition", "Observation"}:
                    system = "http://snomed.info/sct" if resource_type == "Condition" else "http://loinc.org"
                    codes = get_codes(resource, system)

                    if (
                        resource_type == "Condition"
                        and codes & TYPE_2_DIABETES_CODES
                    ):
                        file_markers["type_2_diabetes"] += 1

                    if (
                        resource_type == "Observation"
                        and codes & HBA1C_CODES
                    ):
                        file_markers["hba1c"] += 1

                    if (
                        resource_type == "Observation"
                        and codes & BLOOD_PRESSURE_CODES
                    ):
                        file_markers["blood_pressure"] += 1

            resource_counts.update(file_counts)
            patient_ids.update(file_patients)
            for name in markers:
                markers[name] += file_markers[name]
            valid_files += 1

        except (json.JSONDecodeError, OSError, ValueError) as error:
            invalid_files.append(
                {
                    "file": file_path.relative_to(directory).as_posix(),
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
            "Usage: python -m scripts.cohorts.profile_fhir <fhir-directory>",
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
