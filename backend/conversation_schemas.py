from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from backend.services.user_response import QUESTIONS
from backend.services.session_store import validate_session_id


class ConversationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_field: str | None = None
    skipped_fields: list[str] = Field(default_factory=list, max_length=len(QUESTIONS))

    @field_validator("answer_field")
    @classmethod
    def valid_field(cls, value):
        if value is not None and value not in QUESTIONS:
            raise ValueError("Unknown question field.")
        return value

    @field_validator("skipped_fields")
    @classmethod
    def valid_skips(cls, value):
        if any(field not in QUESTIONS for field in value):
            raise ValueError("Unknown skipped question.")
        return list(dict.fromkeys(value))


class ConversationInput(ConversationOptions):
    text: str = Field(default="", max_length=4000)
    session_id: str = "default"
    skip: StrictBool = False

    @field_validator("session_id")
    @classmethod
    def valid_session(cls, value):
        return validate_session_id(value)

    @model_validator(mode="after")
    def valid_turn(self):
        self.text = self.text.strip()
        if self.skip:
            if not self.answer_field or self.answer_field not in self.skipped_fields:
                raise ValueError("Specify the question to skip.")
        elif not self.text:
            raise ValueError("Please enter a message.")
        return self
