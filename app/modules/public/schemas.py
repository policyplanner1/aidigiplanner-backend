from pydantic import BaseModel, EmailStr, Field


class DemoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    company: str = Field(min_length=1, max_length=255)
    message: str = Field(default="", max_length=2000)


class DemoRequestResponse(BaseModel):
    message: str
