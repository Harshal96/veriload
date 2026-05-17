import pytest

from veriload.data import Company, Contact, Job, Person, PersonaRecord


@pytest.fixture
def sample_persona() -> PersonaRecord:
    return PersonaRecord(
        persona_id="p1",
        locale="en_US",
        person=Person(name="Ada Lovelace", username="ada"),
        contact=Contact(email="ada@example.invalid", phone_e164="+15550000001"),
        job=Job(title="Principal Engineer", industry="Computing"),
        company=Company(id="company-1", name="Analytical Engines", industry="Computing"),
    )
