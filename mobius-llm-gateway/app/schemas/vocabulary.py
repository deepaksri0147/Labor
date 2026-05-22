from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


class InjectionMode(str, Enum):
    prepend = "prepend"
    append = "append"
    replace = "replace"


class Tone(str, Enum):
    formal = "formal"
    casual = "casual"
    friendly = "friendly"
    persuasive = "persuasive"
    technical = "technical"
    simple = "simple"
    professional = "professional"


class TargetAudience(str, Enum):
    children = "children"
    teenagers = "teenagers"
    adults = "adults"
    experts = "experts"


class ResponseFormat(str, Enum):
    conversational = "conversational"
    bullet_points = "bullet_points"
    numbered_list = "numbered_list"
    markdown = "markdown"


class ResponseLength(str, Enum):
    brief = "brief"
    moderate = "moderate"
    detailed = "detailed"


class VocabularyExample(BaseModel):
    user: str = Field(..., description="Example user message")
    assistant: str = Field(..., description="Expected assistant response")


class VocabularyCreate(BaseModel):
    name: str = Field(..., description="Unique name for this vocabulary")
    description: Optional[str] = Field(None, description="What this vocabulary is for")
    instructions: str = Field(
        ...,
        description="Core persona or behavior instructions injected into the system prompt"
    )
    injection_mode: InjectionMode = Field(
        default=InjectionMode.prepend,
        description="prepend: adds before system prompt | append: adds after | replace: replaces entirely"
    )
    tone: Optional[Tone] = Field(
        None,
        description="Communication tone: formal, casual, friendly, persuasive, technical, simple, professional"
    )
    target_audience: Optional[TargetAudience] = Field(
        None,
        description="Who the model is talking to: children, teenagers, adults, experts"
    )
    response_format: Optional[ResponseFormat] = Field(
        None,
        description="How to structure responses: conversational, bullet_points, numbered_list, markdown"
    )
    response_length: Optional[ResponseLength] = Field(
        None,
        description="How long responses should be: brief, moderate, detailed"
    )
    required_phrases: Optional[List[str]] = Field(
        None,
        description="Phrases the model must use in every response (at least 3 from this list)"
    )
    avoid_words: Optional[List[str]] = Field(
        None,
        description="Words the model must never use"
    )
    forbidden_topics: Optional[List[str]] = Field(
        None,
        description="Topics the model must not discuss"
    )
    greeting_style: Optional[str] = Field(
        None,
        description="How to open every response"
    )
    closing_style: Optional[str] = Field(
        None,
        description="How to end every response"
    )
    language: Optional[str] = Field(
        None,
        description="Force responses in a specific language e.g. 'en', 'es', 'fr', 'de'"
    )
    examples: Optional[List[VocabularyExample]] = Field(
        None,
        description="Few-shot examples showing exact expected input/output behavior"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "sales-manager",
                "description": "Luxury retail sales manager persona",
                "instructions": "You are an experienced luxury retail sales manager with 15 years of experience. Be warm, confident, and genuinely helpful — never pushy.",
                "injection_mode": "prepend",
                "tone": "persuasive",
                "target_audience": "adults",
                "response_format": "conversational",
                "response_length": "moderate",
                "required_phrases": [
                    "excellent choice",
                    "suits you perfectly",
                    "our customers love this",
                    "great value for what you get",
                    "you won't regret this"
                ],
                "avoid_words": ["cheap", "basic", "simple", "ordinary", "maybe"],
                "forbidden_topics": ["competitor products", "pricing discounts"],
                "greeting_style": "Always start with a compliment about the customer's taste",
                "closing_style": "Always end with an encouraging statement like 'you won't regret this choice'",
                "language": "en",
                "examples": [
                    {
                        "user": "Tell me about this red jacket",
                        "assistant": "What an excellent choice! This red jacket suits you perfectly. Our customers absolutely love this style — it really brings out your personality. Great value for what you get, and you won't regret this!"
                    }
                ]
            }
        }


class VocabularyResponse(BaseModel):
    vocabulary_id: str
    name: str
    description: Optional[str] = None
    instructions: str
    injection_mode: InjectionMode
    tone: Optional[Tone] = None
    target_audience: Optional[TargetAudience] = None
    response_format: Optional[ResponseFormat] = None
    response_length: Optional[ResponseLength] = None
    required_phrases: Optional[List[str]] = None
    avoid_words: Optional[List[str]] = None
    forbidden_topics: Optional[List[str]] = None
    greeting_style: Optional[str] = None
    closing_style: Optional[str] = None
    language: Optional[str] = None
    examples: Optional[List[VocabularyExample]] = None
