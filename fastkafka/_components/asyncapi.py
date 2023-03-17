# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/013_AsyncAPI.ipynb.

# %% auto 0
__all__ = ['logger', 'ConsumeCallable', 'ProduceCallable', 'sec_scheme_name_mapping', 'KafkaMessage', 'SecurityType',
           'APIKeyLocation', 'SecuritySchema', 'KafkaBroker', 'ContactInfo', 'KafkaServiceInfo', 'KafkaBrokers',
           'yaml_file_cmp', 'export_async_spec']

# %% ../../nbs/013_AsyncAPI.ipynb 1
import json
import shutil
import subprocess  # nosec: B404: Consider possible security implications associated with the subprocess module.
import tempfile
from datetime import timedelta
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import *

from pydantic import BaseModel, Field, HttpUrl
from pydantic.json import timedelta_isoformat
from pydantic.schema import schema

import fastkafka._components.logger

fastkafka._components.logger.should_supress_timestamps = True

import fastkafka
from .logger import get_logger

# %% ../../nbs/013_AsyncAPI.ipynb 3
logger = get_logger(__name__)

# %% ../../nbs/013_AsyncAPI.ipynb 5
ConsumeCallable = Callable[[BaseModel], Union[Awaitable[None], None]]
ProduceCallable = Callable[..., Union[Awaitable[BaseModel], BaseModel]]

# %% ../../nbs/013_AsyncAPI.ipynb 6
class KafkaMessage(BaseModel):
    class Config:
        """This class is used for specific JSON encoders, in our case to properly format timedelta in ISO format."""

        json_encoders = {
            timedelta: timedelta_isoformat,
        }

# %% ../../nbs/013_AsyncAPI.ipynb 8
class SecurityType(str, Enum):
    plain = "plain"
    userPassword = "userPassword"
    apiKey = "apiKey"
    X509 = "X509"
    symmetricEncryption = "symmetricEncryption"
    asymmetricEncryption = "asymmetricEncryption"
    httpApiKey = "httpApiKey"
    http = "http"
    oauth2 = "oauth2"
    openIdConnect = "openIdConnect"
    scramSha256 = "scramSha256"
    scramSha512 = "scramSha512"
    gssapi = "gssapi"


class APIKeyLocation(str, Enum):
    user = "user"
    password = "password"  # nosec
    query = "query"
    header = "header"
    cookie = "cookie"


sec_scheme_name_mapping = {"security_type": "type", "api_key_loc": "in"}


class SecuritySchema(BaseModel):
    security_type: SecurityType = Field(..., example="plain")
    description: Optional[str] = Field(None, example="My security scheme")
    name: Optional[str] = Field(None, example="my_secret_scheme")
    api_key_loc: Optional[APIKeyLocation] = Field(None, example="user")
    scheme: Optional[str] = None
    bearerFormat: Optional[str] = None
    flows: Optional[str] = None
    openIdConnectUrl: Optional[str] = None

    def __init__(self, **kwargs: Any):
        for k, v in sec_scheme_name_mapping.items():
            if v in kwargs:
                kwargs[k] = kwargs.pop(v)
        super().__init__(**kwargs)

    def dict(self, *args: Any, **kwarg: Any) -> Dict[str, Any]:
        """Renames internal names of members ('security_type' -> 'type', 'api_key_loc' -> 'in')"""
        d = super().dict(*args, **kwarg)

        for k, v in sec_scheme_name_mapping.items():
            d[v] = d.pop(k)

        # removes None values
        d = {k: v for k, v in d.items() if v is not None}

        return d

    def json(self, *args: Any, **kwargs: Any) -> str:
        """Serialize into JSON using dict()"""
        return json.dumps(self.dict(), *args, **kwargs)

# %% ../../nbs/013_AsyncAPI.ipynb 10
class KafkaBroker(BaseModel):
    """Kafka broker"""

    url: str = Field(..., example="localhost")
    description: str = Field("Kafka broker")
    port: str = Field("9092")
    protocol: str = Field("kafka")
    security: Optional[SecuritySchema] = None

    def dict(self, *args: Any, **kwarg: Any) -> Dict[str, Any]:
        """Makes port a variable and remove it from the dictionary"""
        d = super().dict(*args, **kwarg)
        d["variables"] = {"port": {"default": self.port}}
        d.pop("port")

        d = {k: v for k, v in d.items() if v is not None}

        return d

    def json(self, *args: Any, **kwargs: Any) -> str:
        """Serialize into JSON using dict()"""
        return json.dumps(self.dict(), *args, **kwargs)

# %% ../../nbs/013_AsyncAPI.ipynb 13
class ContactInfo(BaseModel):
    name: str = Field(..., example="My company")
    url: HttpUrl = Field(..., example="https://www.github.com/mycompany")
    email: str = Field(..., example="noreply@mycompany.com")


class KafkaServiceInfo(BaseModel):
    title: str = Field("Title")
    version: str = Field("0.0.1")
    description: str = Field("Description of the service")
    contact: ContactInfo = Field(
        ...,
    )

# %% ../../nbs/013_AsyncAPI.ipynb 15
class KafkaBrokers(BaseModel):
    brokers: Dict[str, KafkaBroker]

# %% ../../nbs/013_AsyncAPI.ipynb 18
# T = TypeVar("T")


def _get_msg_cls_for_producer(f: ProduceCallable) -> Type[Any]:
    types = get_type_hints(f)
    return_type = types.pop("return", type(None))
    # @app.producer must define a return value
    if return_type == type(None):
        raise ValueError(
            f"Producer function must have a defined return value, got {return_type} as return value"
        )
    if not hasattr(return_type, "json"):
        raise ValueError(f"Producer function return value must have json method")
    return return_type  # type: ignore

# %% ../../nbs/013_AsyncAPI.ipynb 21
def _get_msg_cls_for_consumer(f: ConsumeCallable) -> Type[Any]:
    types = get_type_hints(f)
    return_type = types.pop("return", type(None))
    types_list = list(types.values())
    # @app.consumer takes only message argument
    if len(types_list) != 1:
        raise ValueError(
            f"Consumer function must have only one input param, got {types_list}"
        )
    # @app.consumer does not return a value
    if return_type != type(None):
        raise ValueError(
            f"Consumer function cannot return any value, got {return_type}"
        )
    return types_list[0]  # type: ignore

# %% ../../nbs/013_AsyncAPI.ipynb 24
def _get_topic_dict(
    f: Callable[[Any], Any], direction: str = "publish"
) -> Dict[str, Any]:
    if not direction in ["publish", "subscribe"]:
        raise ValueError(
            f"direction must be one of ['publish', 'subscribe'], but it is '{direction}'."
        )

    #     msg_cls = None

    if direction == "publish":
        msg_cls = _get_msg_cls_for_producer(f)
    elif direction == "subscribe":
        msg_cls = _get_msg_cls_for_consumer(f)

    msg_schema = {"message": {"$ref": f"#/components/messages/{msg_cls.__name__}"}}
    if f.__doc__ is not None:
        msg_schema["description"] = f.__doc__  # type: ignore
    return {direction: msg_schema}

# %% ../../nbs/013_AsyncAPI.ipynb 27
def _get_channels_schema(
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    topics = {}
    for ms, d in zip([consumers, producers], ["subscribe", "publish"]):
        for topic, f in ms.items():  # type: ignore
            topics[topic] = _get_topic_dict(f, d)
    return topics

# %% ../../nbs/013_AsyncAPI.ipynb 29
def _get_kafka_msg_classes(
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
) -> Set[Type[BaseModel]]:
    fc = [_get_msg_cls_for_consumer(consumer) for consumer in consumers.values()]
    fp = [_get_msg_cls_for_producer(producer) for producer in producers.values()]
    return set(fc + fp)


def _get_kafka_msg_definitions(
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
) -> Dict[str, Dict[str, Any]]:
    return schema(_get_kafka_msg_classes(consumers, producers))  # type: ignore

# %% ../../nbs/013_AsyncAPI.ipynb 31
def _get_example(cls: Type[BaseModel]) -> BaseModel:
    kwargs: Dict[str, Any] = {}
    for k, v in cls.__fields__.items():
        #         try:
        if (
            hasattr(v, "field_info")
            and hasattr(v.field_info, "extra")
            and "example" in v.field_info.extra
        ):
            example = v.field_info.extra["example"]
            kwargs[k] = example
    #         except:
    #             pass

    return json.loads(cls(**kwargs).json())  # type: ignore

# %% ../../nbs/013_AsyncAPI.ipynb 33
def _add_example_to_msg_definitions(
    msg_cls: Type[BaseModel], msg_schema: Dict[str, Dict[str, Any]]
) -> None:
    try:
        example = _get_example(msg_cls)
    except Exception as e:
        example = None
    if example is not None:
        msg_schema["definitions"][msg_cls.__name__]["example"] = example


def _get_msg_definitions_with_examples(
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
) -> Dict[str, Dict[str, Any]]:
    msg_classes = _get_kafka_msg_classes(consumers, producers)
    msg_schema: Dict[str : Dict[str, Any]] = schema(msg_classes)  # type: ignore
    for msg_cls in msg_classes:
        _add_example_to_msg_definitions(msg_cls, msg_schema)

    msg_schema = (
        {k: {"payload": v} for k, v in msg_schema["definitions"].items()}
        if "definitions" in msg_schema
        else {}
    )

    return msg_schema

# %% ../../nbs/013_AsyncAPI.ipynb 35
def _get_security_schemes(kafka_brokers: KafkaBrokers) -> Dict[str, Any]:
    security_schemes = {}
    for key, kafka_broker in kafka_brokers.brokers.items():
        if kafka_broker.security is not None:
            security_schemes[f"{key}_default_security"] = json.loads(
                kafka_broker.security.json()
            )
    return security_schemes

# %% ../../nbs/013_AsyncAPI.ipynb 37
def _get_components_schema(
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
    kafka_brokers: KafkaBrokers,
) -> Dict[str, Any]:
    definitions = _get_msg_definitions_with_examples(consumers, producers)
    msg_classes = [cls.__name__ for cls in _get_kafka_msg_classes(consumers, producers)]
    components = {
        "messages": {k: v for k, v in definitions.items() if k in msg_classes},
        "schemas": {k: v for k, v in definitions.items() if k not in msg_classes},
        "securitySchemes": _get_security_schemes(kafka_brokers),
    }
    substitutions = {
        f"#/definitions/{k}": f"#/components/messages/{k}"
        if k in msg_classes
        else f"#/components/schemas/{k}"
        for k in definitions.keys()
    }

    def _sub_values(d: Any, substitutions: Dict[str, str] = substitutions) -> Any:
        if isinstance(d, dict):
            d = {k: _sub_values(v) for k, v in d.items()}
        if isinstance(d, list):
            d = [_sub_values(k) for k in d]
        elif isinstance(d, str):
            for k, v in substitutions.items():
                if d == k:
                    d = v
        return d

    return _sub_values(components)  # type: ignore

# %% ../../nbs/013_AsyncAPI.ipynb 39
def _get_servers_schema(kafka_brokers: KafkaBrokers) -> Dict[str, Any]:
    servers = json.loads(kafka_brokers.json(sort_keys=False))["brokers"]

    for key, kafka_broker in servers.items():
        if "security" in kafka_broker:
            servers[key]["security"] = [{f"{key}_default_security": []}]
    return servers  # type: ignore

# %% ../../nbs/013_AsyncAPI.ipynb 41
def _get_asyncapi_schema(
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
    kafka_brokers: KafkaBrokers,
    kafka_service_info: KafkaServiceInfo,
) -> Dict[str, Any]:
    #     # we don't use dict because we need custom JSON encoders
    info = json.loads(kafka_service_info.json(sort_keys=False))
    servers = _get_servers_schema(kafka_brokers)
    #     # should be in the proper format already
    channels = _get_channels_schema(consumers, producers)
    components = _get_components_schema(consumers, producers, kafka_brokers)
    return {
        "asyncapi": "2.5.0",
        "info": info,
        "servers": servers,
        "channels": channels,
        "components": components,
    }

# %% ../../nbs/013_AsyncAPI.ipynb 43
def yaml_file_cmp(file_1: Union[Path, str], file_2: Union[Path, str]) -> bool:
    try:
        import yaml
    except Exception as e:
        msg = "Please install docs version of fastkafka using 'pip install fastkafka[docs]' command"
        logger.error(msg)
        raise RuntimeError(msg)

    def _read(f: Union[Path, str]) -> Dict[str, Any]:
        with open(f) as stream:
            return yaml.safe_load(stream)  # type: ignore

    d = [_read(f) for f in [file_1, file_2]]
    return d[0] == d[1]

# %% ../../nbs/013_AsyncAPI.ipynb 44
def _generate_async_spec(
    *,
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
    kafka_brokers: KafkaBrokers,
    kafka_service_info: KafkaServiceInfo,
    spec_path: Path,
    force_rebuild: bool,
) -> bool:
    try:
        import yaml
    except Exception as e:
        msg = "Please install docs version of fastkafka using 'pip install fastkafka[docs]' command"
        logger.error(msg)
        raise RuntimeError(msg)

    # generate spec file
    asyncapi_schema = _get_asyncapi_schema(
        consumers, producers, kafka_brokers, kafka_service_info
    )
    if not spec_path.exists():
        logger.info(
            f"Old async specifications at '{spec_path.resolve()}' does not exist."
        )
    spec_path.parent.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory() as d:
        with open(Path(d) / "asyncapi.yml", "w") as f:
            yaml.dump(asyncapi_schema, f, sort_keys=False)
        spec_changed = not (
            spec_path.exists() and yaml_file_cmp(Path(d) / "asyncapi.yml", spec_path)
        )
        if spec_changed or force_rebuild:
            shutil.copyfile(Path(d) / "asyncapi.yml", spec_path)
            logger.info(
                f"New async specifications generated at: '{spec_path.resolve()}'"
            )
            return True
        else:
            logger.info(
                f"Keeping the old async specifications at: '{spec_path.resolve()}'"
            )
            return False

# %% ../../nbs/013_AsyncAPI.ipynb 46
def _install_docs_deps() -> None:
    with TemporaryDirectory() as d:
        cmd = [
            "npx",
            "-y",
            "-p",
            "@asyncapi/generator",
            "ag",
            "https://raw.githubusercontent.com/asyncapi/asyncapi/master/examples/simple.yml",
            "@asyncapi/html-template",
            "-o",
            d,
        ]

        p = subprocess.run(  # nosec: B603 subprocess call - check for execution of untrusted input.
            cmd, stderr=subprocess.STDOUT, stdout=subprocess.PIPE
        )
        if p.returncode == 0:
            logger.info("AsyncAPI generator installed")
        else:
            logger.error("AsyncAPI generator NOT installed!")
            logger.info(f"Output of '$ {' '.join(cmd)}'{p.stdout.decode()}")
            raise ValueError(
                f"AsyncAPI generator NOT installed, used '$ {' '.join(cmd)}'{p.stdout.decode()}"
            )


def _generate_async_docs(
    *,
    spec_path: Path,
    docs_path: Path,
) -> None:
    cmd = [
        "npx",
        "-y",
        "-p",
        "@asyncapi/generator",
        "ag",
        f"{spec_path}",
        "@asyncapi/html-template",
        "-o",
        f"{docs_path}",
        "--force-write",
    ]
    p = subprocess.run(  # nosec: B603 subprocess call - check for execution of untrusted input.
        cmd, stderr=subprocess.STDOUT, stdout=subprocess.PIPE
    )
    if p.returncode == 0:
        logger.info(f"Async docs generated at '{docs_path}'")
        logger.info(f"Output of '$ {' '.join(cmd)}'{p.stdout.decode()}")
    else:
        logger.error(f"Generation of async docs failed!")
        logger.info(f"Output of '$ {' '.join(cmd)}'{p.stdout.decode()}")
        raise ValueError(
            f"Generation of async docs failed, used '$ {' '.join(cmd)}'{p.stdout.decode()}"
        )

# %% ../../nbs/013_AsyncAPI.ipynb 48
def export_async_spec(
    *,
    consumers: Dict[str, ConsumeCallable],
    producers: Dict[str, ProduceCallable],
    kafka_brokers: KafkaBrokers,
    kafka_service_info: KafkaServiceInfo,
    asyncapi_path: Union[Path, str],
    force_rebuild: bool = True,
) -> None:
    """Export async specification to a given path

    Params:
        path: path where the specification will be exported. If parent subdirectories do not exist, they will be created.


    """
    # generate spec file
    spec_path = Path(asyncapi_path) / "spec" / "asyncapi.yml"
    is_spec_built = _generate_async_spec(
        consumers=consumers,
        producers=producers,
        kafka_brokers=kafka_brokers,
        kafka_service_info=kafka_service_info,
        spec_path=spec_path,
        force_rebuild=force_rebuild,
    )

    # generate docs folder
    docs_path = Path(asyncapi_path) / "docs"

    if not is_spec_built and docs_path.exists():
        logger.info(
            f"Skipping generating async documentation in '{docs_path.resolve()}'"
        )
        return

    _generate_async_docs(
        spec_path=spec_path,
        docs_path=docs_path,
    )
