import hashlib
import json
import os
import sys
from dotenv import load_dotenv
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")


def get_json_files(directory):
    return sorted(directory.rglob("*.json"))


def calculate_fingerprint(directory, files):
    digest = hashlib.sha256()

    for file_path in files:
        relative_name = file_path.relative_to(directory).as_posix()

        digest.update(relative_name.encode())
        digest.update(b"\0")
        digest.update(file_path.read_bytes())

    return digest.hexdigest()


def get_resource(entry):
    if not isinstance(entry, dict):
        raise ValueError("Bundle entry is not an object")
    resource = entry.get("resource")

    if isinstance(resource, dict):
        return resource

    raise ValueError("Bundle entry does not contain a resource object")


def build_reference_map(entries):
    references = {}

    for entry in entries:
        resource = get_resource(entry)

        if not resource:
            continue

        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")

        if not resource_type or not resource_id:
            continue

        target = (resource_type, resource_id)

        references[f"{resource_type}/{resource_id}"] = target

        full_url = entry.get("fullUrl")

        if full_url:
            references[full_url] = target

    return references


def resolve_reference(reference, references, expected_type):
    if not reference:
        return None

    target = references.get(reference)

    if target and target[0] == expected_type:
        return target[1]

    prefix = f"{expected_type}/"

    if reference.startswith(prefix):
        return reference.removeprefix(prefix)

    return None


def get_reference(resource, field_name):
    field = resource.get(field_name)

    if not isinstance(field, dict):
        return None

    return field.get("reference")


def get_patient_id(resource, references):
    if resource.get("resourceType") == "Patient":
        return resource.get("id")

    for field_name in ("subject", "patient", "beneficiary"):
        reference = get_reference(resource, field_name)

        patient_id = resolve_reference(
            reference,
            references,
            "Patient",
        )

        if patient_id:
            return patient_id

    return None


def first_coding(concept, preferred_system=None):
    if not isinstance(concept, dict):
        return None, None, None

    codings = concept.get("coding", [])

    if not isinstance(codings, list) or not codings:
        return None, None, concept.get("text")

    coding = next(
        (item for item in codings if isinstance(item, dict) and item.get("system") == preferred_system),
        codings[0],
    ) if preferred_system else codings[0]

    if not isinstance(coding, dict):
        return None, None, concept.get("text")

    return (
        coding.get("system"),
        coding.get("code"),
        coding.get("display") or concept.get("text"),
    )


def get_status(resource):
    _, code, display = first_coding(
        resource.get("clinicalStatus")
    )

    return code or display


def get_verification_status(resource):
    return first_coding(resource.get("verificationStatus"))[1]


def get_blood_pressure(resource):
    """Keep panel components on the parent row, without inventing FHIR IDs."""
    values = {"8480-6": (None, None), "8462-4": (None, None)}
    components = resource.get("component", [])
    if not isinstance(components, list):
        raise ValueError("Observation.component is not a list")
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("Observation component is not an object")
        system, code, _ = first_coding(component.get("code"), "http://loinc.org")
        quantity = component.get("valueQuantity") or {}
        if system == "http://loinc.org" and code in values:
            if not isinstance(quantity, dict):
                raise ValueError("Component valueQuantity is not an object")
            if values[code][0] is not None:
                raise ValueError(f"Duplicate blood-pressure component {code}")
            values[code] = (quantity.get("value"), quantity.get("unit") or quantity.get("code"))
    return (*values["8480-6"], *values["8462-4"])


def get_observation_date(resource):
    observed_at = resource.get("effectiveDateTime")

    if observed_at:
        return observed_at

    effective_period = resource.get("effectivePeriod")

    if isinstance(effective_period, dict):
        if effective_period.get("start"):
            return effective_period["start"]

    return resource.get("issued")


def get_text_value(resource):
    if resource.get("valueString") is not None:
        return resource["valueString"]

    value_concept = resource.get("valueCodeableConcept")

    if isinstance(value_concept, dict):
        _, _, display = first_coding(value_concept)
        return display

    return None


def insert_patient(cursor, run_id, resource):
    cursor.execute(
        """
        INSERT INTO patients (
            run_id,
            patient_id,
            birth_date,
            gender,
            deceased_at
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (run_id, patient_id) DO NOTHING
        """,
        (
            run_id,
            resource["id"],
            resource.get("birthDate"),
            resource.get("gender"),
            resource.get("deceasedDateTime"),
        ),
    )


def insert_condition(
    cursor,
    run_id,
    resource,
    references,
):
    patient_id = get_patient_id(resource, references)

    if not patient_id:
        raise ValueError(
            f"Condition {resource.get('id')} has no patient"
        )

    system, code, display = first_coding(
        resource.get("code"), "http://snomed.info/sct"
    )

    cursor.execute(
        """
        INSERT INTO conditions (
            run_id,
            condition_id,
            patient_id,
            code_system,
            code,
            display,
            clinical_status,
            verification_status,
            onset_at,
            abatement_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (run_id, condition_id) DO NOTHING
        """,
        (
            run_id,
            resource["id"],
            patient_id,
            system,
            code,
            display,
            get_status(resource),
            get_verification_status(resource),
            resource.get("onsetDateTime"),
            resource.get("abatementDateTime"),
        ),
    )


def insert_observation(
    cursor,
    run_id,
    resource,
    references,
):
    patient_id = get_patient_id(resource, references)

    if not patient_id:
        raise ValueError(
            f"Observation {resource.get('id')} has no patient"
        )

    encounter_id = resolve_reference(
        get_reference(resource, "encounter"),
        references,
        "Encounter",
    )

    system, code, display = first_coding(
        resource.get("code"), "http://loinc.org"
    )

    value_quantity = resource.get("valueQuantity", {})

    if not isinstance(value_quantity, dict):
        value_quantity = {}

    cursor.execute(
        """
        INSERT INTO observations (
            run_id,
            observation_id,
            patient_id,
            encounter_id,
            code_system,
            code,
            display,
            observed_at,
            numeric_value,
            text_value,
            unit,
            systolic_value,
            systolic_unit,
            diastolic_value,
            diastolic_unit
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (run_id, observation_id)
        DO NOTHING
        """,
        (
            run_id,
            resource["id"],
            patient_id,
            encounter_id,
            system,
            code,
            display,
            get_observation_date(resource),
            value_quantity.get("value"),
            get_text_value(resource),
            value_quantity.get("unit"),
            *get_blood_pressure(resource),
        ),
    )


def load_bundle(
    cursor,
    run_id,
    file_path,
    directory,
):
    with file_path.open(encoding="utf-8") as file:
        bundle = json.load(file)

    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError(
            f"{file_path.name} is not a FHIR Bundle"
        )

    entries = bundle.get("entry")

    if not isinstance(entries, list):
        raise ValueError(
            f"{file_path.name} has invalid Bundle.entry"
        )

    references = build_reference_map(entries)

    # Insert patients first so foreign keys work.
    for entry in entries:
        resource = get_resource(entry)

        if (
            resource
            and resource.get("resourceType") == "Patient"
            and resource.get("id")
        ):
            insert_patient(cursor, run_id, resource)

    for index, entry in enumerate(entries):
        resource = get_resource(entry)

        if not resource:
            continue

        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")

        if not resource_type:
            raise ValueError(
                f"{file_path.name}, entry {index}: "
                "missing resourceType"
            )

        patient_id = get_patient_id(
            resource,
            references,
        )

        cursor.execute(
            """
            INSERT INTO raw_fhir_resources (
                run_id,
                file_name,
                entry_index,
                resource_type,
                resource_id,
                patient_id,
                resource_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                file_path.relative_to(directory).as_posix(),
                index,
                resource_type,
                resource_id,
                patient_id,
                Jsonb(resource),
            ),
        )

        if resource_type == "Condition":
            insert_condition(
                cursor,
                run_id,
                resource,
                references,
            )

        elif resource_type == "Observation":
            insert_observation(
                cursor,
                run_id,
                resource,
                references,
            )


def print_counts(cursor, run_id):
    tables = [
        "raw_fhir_resources",
        "patients",
        "conditions",
        "observations",
    ]

    print("\nImported rows:")

    for table in tables:
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE run_id = %s",
            (run_id,),
        )

        count = cursor.fetchone()[0]
        print(f"  {table}: {count}")


def load_dataset(directory):
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is missing; configure the project .env")
    files = get_json_files(directory)

    if not files:
        raise ValueError(
            f"No JSON files found in {directory}"
        )

    fingerprint = calculate_fingerprint(
        directory,
        files,
    )

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id
                FROM ingestion_runs
                WHERE dataset_fingerprint = %s
                """,
                (fingerprint,),
            )

            existing = cursor.fetchone()

            if existing:
                print(
                    "Dataset already loaded as run "
                    f"{existing[0]}"
                )
                return

            cursor.execute(
                """
                INSERT INTO ingestion_runs (
                    dataset_fingerprint,
                    source_name
                )
                VALUES (%s, 'Synthea')
                RETURNING run_id
                """,
                (fingerprint,),
            )

            run_id = cursor.fetchone()[0]

            for number, file_path in enumerate(
                files,
                start=1,
            ):
                print(
                    f"[{number}/{len(files)}] "
                    f"{file_path.name}"
                )

                load_bundle(
                    cursor,
                    run_id,
                    file_path,
                    directory,
                )

            cursor.execute(
                """
                UPDATE ingestion_runs
                SET
                    status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    as_of_date = (
                        SELECT MAX(observed_at)
                        FROM observations
                        WHERE run_id = %s
                    )
                WHERE run_id = %s
                RETURNING as_of_date
                """,
                (run_id, run_id),
            )

            as_of_date = cursor.fetchone()[0]

            print_counts(cursor, run_id)
            print(f"\nRun ID: {run_id}")
            print(f"Dataset fingerprint: {fingerprint}")
            print(f"Dataset as-of date: {as_of_date}")


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python3 scripts/load_fhir.py "
            "<FHIR-directory>",
            file=sys.stderr,
        )
        return 1

    directory = Path(sys.argv[1]).resolve()

    if not directory.is_dir():
        print(
            f"Directory not found: {directory}",
            file=sys.stderr,
        )
        return 1

    try:
        load_dataset(directory)
    except Exception as error:
        print(f"Import failed: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
