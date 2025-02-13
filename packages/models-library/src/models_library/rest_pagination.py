from typing import Generic, List, Optional, TypeVar

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Extra,
    Field,
    NonNegativeInt,
    PositiveInt,
    validator,
)
from pydantic.generics import GenericModel

DEFAULT_NUMBER_OF_ITEMS_PER_PAGE = 20


class PageMetaInfoLimitOffset(BaseModel):
    limit: PositiveInt = DEFAULT_NUMBER_OF_ITEMS_PER_PAGE
    total: NonNegativeInt
    offset: NonNegativeInt = 0
    count: NonNegativeInt

    @validator("offset")
    @classmethod
    def check_offset(cls, v, values):
        if v > 0 and v >= values["total"]:
            raise ValueError(
                f"offset {v} cannot be equal or bigger than total {values['total']}, please check"
            )
        return v

    @validator("count")
    @classmethod
    def check_count(cls, v, values):
        if v > values["limit"]:
            raise ValueError(
                f"count {v} bigger than limit {values['limit']}, please check"
            )
        if v > values["total"]:
            raise ValueError(
                f"count {v} bigger than expected total {values['total']}, please check"
            )
        if "offset" in values and (values["offset"] + v) > values["total"]:
            raise ValueError(
                f"offset {values['offset']} + count {v} is bigger than allowed total {values['total']}, please check"
            )
        return v

    class Config:
        extra = Extra.forbid

        schema_extra = {
            "examples": [
                {"total": 7, "count": 4, "limit": 4, "offset": 0},
            ]
        }


class PageLinks(BaseModel):
    self: AnyHttpUrl
    first: AnyHttpUrl
    prev: Optional[AnyHttpUrl]
    next: Optional[AnyHttpUrl]
    last: AnyHttpUrl

    class Config:
        extra = Extra.forbid


ItemT = TypeVar("ItemT")


class Page(GenericModel, Generic[ItemT]):
    """
    Paginated response model of ItemTs
    """

    meta: PageMetaInfoLimitOffset = Field(alias="_meta")
    links: PageLinks = Field(alias="_links")
    data: List[ItemT]

    @validator("data", pre=True)
    @classmethod
    def convert_none_to_empty_list(cls, v):
        if v is None:
            v = []
        return v

    @validator("data")
    @classmethod
    def check_data_compatible_with_meta(cls, v, values):
        if "meta" not in values:
            # if the validation failed in meta this happens
            raise ValueError("meta not in values")
        if len(v) != values["meta"].count:
            raise ValueError(
                f"container size [{len(v)}] must be equal to count [{values['meta'].count}]"
            )
        return v

    class Config:
        extra = Extra.forbid

        schema_extra = {
            "examples": [
                # first page Page[str]
                {
                    "_meta": {"total": 7, "count": 4, "limit": 4, "offset": 0},
                    "_links": {
                        "self": "http://osparc.io/v2/listing?offset=0&limit=4",
                        "first": "http://osparc.io/v2/listing?offset=0&limit=4",
                        "prev": None,
                        "next": "http://osparc.io/v2/listing?offset=1&limit=4",
                        "last": "http://osparc.io/v2/listing?offset=1&limit=4",
                    },
                    "data": ["data 1", "data 2", "data 3", "data 4"],
                },
                # second and last page
                {
                    "_meta": {"total": 7, "count": 3, "limit": 4, "offset": 1},
                    "_links": {
                        "self": "http://osparc.io/v2/listing?offset=1&limit=4",
                        "first": "http://osparc.io/v2/listing?offset=0&limit=4",
                        "prev": "http://osparc.io/v2/listing?offset=0&limit=4",
                        "next": None,
                        "last": "http://osparc.io/v2/listing?offset=1&limit=4",
                    },
                    "data": ["data 5", "data 6", "data 7"],
                },
            ]
        }
