# coding=utf-8
import json
import logging
import sys
import uuid
from datetime import datetime
import traceback

from json_logging import util
from json_logging.framework_base import RequestAdapter, ResponseAdapter, AppRequestInstrumentationConfigurator, \
    FrameworkConfigurator
from json_logging.util import get_library_logger, is_env_var_toggle

CORRELATION_ID_GENERATOR = uuid.uuid1
ENABLE_JSON_LOGGING = False
if is_env_var_toggle("ENABLE_JSON_LOGGING"):
    ENABLE_JSON_LOGGING = True

ENABLE_JSON_LOGGING_DEBUG = False
EMPTY_VALUE = '-'
CREATE_CORRELATION_ID_IF_NOT_EXISTS = True
JSON_SERIALIZER = lambda log: json.dumps(log, ensure_ascii=False)
CORRELATION_ID_HEADERS = ['X-Correlation-ID', 'X-Request-ID']
COMPONENT_ID = EMPTY_VALUE
COMPONENT_NAME = EMPTY_VALUE
COMPONENT_INSTANCE_INDEX = 0

# The list contains all the attributes listed in
# http://docs.python.org/library/logging.html#logrecord-attributes
RECORD_ATTR_SKIP_LIST = [
    'asctime', 'created', 'exc_info', 'exc_text', 'filename', 'args',
    'funcName', 'id', 'levelname', 'levelno', 'lineno', 'module', 'msg',
    'msecs', 'msecs', 'message', 'name', 'pathname', 'process',
    'processName', 'relativeCreated', 'thread', 'threadName', 'extra',
    # Also exclude legacy 'props'
    'props',
]

try:
    basestring
except NameError:
    basestring = str

if sys.version_info < (3, 0):
    EASY_TYPES = (basestring, bool, dict, float, int, list, type(None))
else:
    RECORD_ATTR_SKIP_LIST.append('stack_info')
    EASY_TYPES = (str, bool, dict, float, int, list, type(None))

_framework_support_map = {}
_current_framework = None
_logger = get_library_logger(__name__)
_request_util = None
_default_formatter = None


def get_correlation_id(request=None):
    """
    Get current request correlation-id. If one is not present, a new one might be generated
    depends on CREATE_CORRELATION_ID_IF_NOT_EXISTS setting value.

    :return: correlation-id string
    """
    return _request_util.get_correlation_id(request=request)


def register_framework_support(name, app_configurator, app_request_instrumentation_configurator, request_adapter_class,
                               response_adapter_class):
    """
    register support for a framework

    :param name: name of framework
    :param app_configurator: app pre-configurator class
    :param app_request_instrumentation_configurator: app configurator class
    :param request_adapter_class: request adapter class
    :param response_adapter_class: response adapter class
    """
    if not name:
        raise RuntimeError("framework name can not be null or empty")

    util.validate_subclass(request_adapter_class, RequestAdapter)
    util.validate_subclass(response_adapter_class, ResponseAdapter)
    util.validate_subclass(app_request_instrumentation_configurator, AppRequestInstrumentationConfigurator)
    if app_configurator is not None:
        util.validate_subclass(app_configurator, FrameworkConfigurator)

    name = name.lower()
    if name in _framework_support_map:
        ENABLE_JSON_LOGGING_DEBUG and _logger.warning("Re-register framework %s", name)
    _framework_support_map[name] = {
        'app_configurator': app_configurator,
        'app_request_instrumentation_configurator': app_request_instrumentation_configurator,
        'request_adapter_class': request_adapter_class,
        'response_adapter_class': response_adapter_class
    }


def config_root_logger():
    """
        You must call this if you are using root logger.
        Make all root logger' handlers produce JSON format
        & remove duplicate handlers for request instrumentation logging.
        Please made sure that you call this after you called "logging.basicConfig() or logging.getLogger()
    """

    if not logging.root.handlers:
        _logger.error(
            "No logging handlers found for root logger. Please made sure that you call this after you called "
            "logging.basicConfig() or logging.getLogger()")
        return

    if ENABLE_JSON_LOGGING:
        ENABLE_JSON_LOGGING_DEBUG and _logger.debug("Update root logger to using JSONLogFormatter")

        global _default_formatter
        util.update_formatter_for_loggers([logging.root], _default_formatter)


def init_non_web(*args, **kw):
    __init(*args, **kw)


class RequestResponseDTOBase(dict):
    """
        Data transfer object for HTTP request & response information for request instrumentation logging
        Any key that is stored in this dict will be appended to final JSON log object
    """

    def __init__(self, request, **kwargs):
        """
        invoked when request start, where to extract any necessary information from the request object
        :param request: request object
        """
        super(RequestResponseDTOBase, self).__init__(**kwargs)
        self._request = request

    def on_request_complete(self, response):
        """
        invoked when request complete, update response information into this object, must be called before invoke request logging statement
        :param response: response object
        """
        self._response = response


class DefaultRequestResponseDTO(RequestResponseDTOBase):
    """
        default implementation
    """

    def __init__(self, request, **kwargs):
        super(DefaultRequestResponseDTO, self).__init__(request, **kwargs)
        utcnow = datetime.utcnow()
        self._request_start = utcnow
        self["request_received_at"] = util.iso_time_format(utcnow)

    # noinspection PyAttributeOutsideInit
    def on_request_complete(self, response):
        super(DefaultRequestResponseDTO, self).on_request_complete(response)
        utcnow = datetime.utcnow()
        time_delta = utcnow - self._request_start
        self["response_time_ms"] = int(time_delta.total_seconds()) * 1000 + int(time_delta.microseconds / 1000)
        self["response_sent_at"] = util.iso_time_format(utcnow)


def __init(framework_name=None, custom_formatter=None, enable_json=False):
    """
    Initialize JSON logging support, if no **framework_name** passed, logging will be initialized in non-web context.
    This is supposed to be called only one time.

    If **custom_formatter** is passed, it will (in non-web context) use this formatter over the default.

    :param framework_name: type of framework logging should support.DEFAULT_CORRELATION_ID_HEADERS
    :param custom_formatter: formatter to override default JSONLogFormatter.
    """

    global _current_framework
    global ENABLE_JSON_LOGGING
    global _default_formatter
    ENABLE_JSON_LOGGING = enable_json
    if _current_framework is not None:
        raise RuntimeError("Can not call init more than once")

    if custom_formatter:
        if not issubclass(custom_formatter, logging.Formatter):
            raise ValueError('custom_formatter is not subclass of logging.Formatter', custom_formatter)

    ENABLE_JSON_LOGGING_DEBUG and _logger.info("init framework " + str(framework_name))

    if framework_name:
        framework_name = framework_name.lower()
        if framework_name not in _framework_support_map.keys():
            raise RuntimeError(framework_name + " is not a supported framework")

        _current_framework = _framework_support_map[framework_name]
        global _request_util
        _request_util = util.RequestUtil(request_adapter_class=_current_framework['request_adapter_class'],
                                         response_adapter_class=_current_framework['response_adapter_class'])

        if ENABLE_JSON_LOGGING and _current_framework['app_configurator'] is not None:
            _current_framework['app_configurator']().config()

        _default_formatter = custom_formatter if custom_formatter else JSONLogWebFormatter
    else:
        _default_formatter = custom_formatter if custom_formatter else JSONLogFormatter

    if not enable_json and not ENABLE_JSON_LOGGING:
        _logger.warning(
            "JSON format is not enabled, normal log will be in plain text but request logging still in JSON format! "
            "To enable set ENABLE_JSON_LOGGING env var to either one of following values: ['true', '1', 'y', 'yes']")
    else:
        ENABLE_JSON_LOGGING = True
        logging._defaultFormatter = _default_formatter()

    # go to all the initialized logger and update it to use JSON formatter
    ENABLE_JSON_LOGGING_DEBUG and _logger.debug("Update all existing logger to using JSONLogFormatter")
    existing_loggers = list(map(logging.getLogger, logging.Logger.manager.loggerDict))
    util.update_formatter_for_loggers(existing_loggers, _default_formatter)


def init_request_instrument(app=None, custom_formatter=None, exclude_url_patterns=[],
                            request_response_data_extractor_class=DefaultRequestResponseDTO):
    """
    Configure the request instrumentation logging configuration for given web app. Must be called after init method

    If **custom_formatter** is passed, it will use this formatter over the default.

    :param app: current web application instance
    :param custom_formatter: formatter to override default JSONRequestLogFormatter.
    :param request_response_data_extractor_class: request_response_data_extractor_class to override default json_logging.RequestResponseDataExtractor.
    """

    if _current_framework is None or _current_framework == '-':
        raise RuntimeError("please init the logging first, call init(framework_name) first")

    if custom_formatter:
        if not issubclass(custom_formatter, logging.Formatter):
            raise ValueError('custom_formatter is not subclass of logging.Formatter', custom_formatter)

    if not issubclass(request_response_data_extractor_class, RequestResponseDTOBase):
        raise ValueError('request_response_data_extractor_class is not subclass of json_logging.RequestInfoBase',
                         custom_formatter)

    configurator = _current_framework['app_request_instrumentation_configurator']()
    configurator.config(app, request_response_data_extractor_class, exclude_url_patterns=exclude_url_patterns)

    formatter = custom_formatter if custom_formatter else JSONRequestLogFormatter
    request_logger = configurator.request_logger
    request_logger.setLevel(logging.DEBUG)
    request_logger.addHandler(logging.StreamHandler(sys.stdout))
    util.update_formatter_for_loggers([request_logger], formatter)
    request_logger.parent = None


def get_request_logger():
    if _current_framework is None or _current_framework == '-':
        raise RuntimeError(
            "request_logger is only available if json_logging is inited with a web app, "
            "call init_<framework_name>() to do that")

    instance = _current_framework['app_request_instrumentation_configurator']._instance
    if instance is None:
        raise RuntimeError("please init request instrument first, call init_request_instrument(app) to do that")

    return instance.request_logger


class BaseJSONFormatter(logging.Formatter):
    """
       Base class for JSON formatters
    """
    base_object_common = {}

    def __init__(self, *args, **kw):
        super(BaseJSONFormatter, self).__init__(*args, **kw)
        if COMPONENT_ID and COMPONENT_ID != EMPTY_VALUE:
            self.base_object_common["component_id"] = COMPONENT_ID
        if COMPONENT_NAME and COMPONENT_NAME != EMPTY_VALUE:
            self.base_object_common["component_name"] = COMPONENT_NAME
        if COMPONENT_INSTANCE_INDEX and COMPONENT_INSTANCE_INDEX != EMPTY_VALUE:
            self.base_object_common["component_instance_idx"] = COMPONENT_INSTANCE_INDEX

    def format(self, record):
        log_object = self._format_log_object(record, request_util=_request_util)
        return JSON_SERIALIZER(log_object)

    def _format_log_object(self, record, request_util):
        utcnow = datetime.utcnow()
        base_obj = {
            "written_at": util.iso_time_format(utcnow),
            "written_ts": util.epoch_nano_second(utcnow),
        }
        base_obj.update(self.base_object_common)
        # Add extra fields
        base_obj.update(self._get_extra_fields(record))
        return base_obj

    def _get_extra_fields(self, record):
        fields = {}

        if record.args:
            fields['msg'] = record.msg

        for key, value in record.__dict__.items():
            if key not in RECORD_ATTR_SKIP_LIST:
                if isinstance(value, EASY_TYPES):
                    fields[key] = value
                else:
                    fields[key] = repr(value)

        # Always add 'props' to the root of the log, assumes props is a dict
        if hasattr(record, 'props') and isinstance(record.props, dict):
            fields.update(record.props)

        return fields


class JSONRequestLogFormatter(BaseJSONFormatter):
    """
       Formatter for HTTP request instrumentation logging
    """

    def _format_log_object(self, record, request_util):
        json_log_object = super(JSONRequestLogFormatter, self)._format_log_object(record, request_util)
        request_adapter = request_util.request_adapter
        response_adapter = _request_util.response_adapter
        request = record.request_response_data._request
        response = record.request_response_data._response

        length = request_adapter.get_content_length(request)

        json_log_object.update({
            "type": "request",
            "correlation_id": request_util.get_correlation_id(request),
            "remote_user": request_adapter.get_remote_user(request),
            "request": request_adapter.get_path(request),
            "referer": request_adapter.get_http_header(request, 'referer', EMPTY_VALUE),
            "x_forwarded_for": request_adapter.get_http_header(request, 'x-forwarded-for', EMPTY_VALUE),
            "protocol": request_adapter.get_protocol(request),
            "method": request_adapter.get_method(request),
            "remote_ip": request_adapter.get_remote_ip(request),
            "request_size_b": util.parse_int(length, -1),
            "remote_host": request_adapter.get_remote_ip(request),
            "remote_port": request_adapter.get_remote_port(request),
            "response_status": response_adapter.get_status_code(response),
            "response_size_b": response_adapter.get_response_size(response),
            "response_content_type": response_adapter.get_content_type(response),
        })

        json_log_object.update(record.request_response_data)

        return json_log_object


def _sanitize_log_msg(record):
    return record.getMessage().replace('\n', '_').replace('\r', '_').replace('\t', '_')


class JSONLogFormatter(BaseJSONFormatter):
    """
    Formatter for non-web application log
    """

    def get_exc_fields(self, record):
        if record.exc_info:
            exc_info = self.format_exception(record.exc_info)
        else:
            exc_info = record.exc_text
        return {
            'exc_info': exc_info,
            'filename': record.filename,
        }

    @classmethod
    def format_exception(cls, exc_info):
        return ''.join(traceback.format_exception(*exc_info)) if exc_info else ''

    def _format_log_object(self, record, request_util):
        json_log_object = super(JSONLogFormatter, self)._format_log_object(record, request_util)
        json_log_object.update({
            "msg": _sanitize_log_msg(record),
            "type": "log",
            "logger": record.name,
            "thread": record.threadName,
            "level": record.levelname,
            "module": record.module,
            "line_no": record.lineno,
        })

        if record.exc_info or record.exc_text:
            json_log_object.update(self.get_exc_fields(record))

        return json_log_object


class JSONLogWebFormatter(JSONLogFormatter):
    """
    Formatter for web application log
    """

    def _format_log_object(self, record, request_util):
        json_log_object = super(JSONLogWebFormatter, self)._format_log_object(record, request_util)
        if "correlation_id" not in json_log_object:
            json_log_object.update({
                "correlation_id": request_util.get_correlation_id(within_formatter=True),
            })
        return json_log_object


# register flask support
# noinspection PyPep8
import json_logging.framework.flask as flask_support

register_framework_support('flask', None, flask_support.FlaskAppRequestInstrumentationConfigurator,
                           flask_support.FlaskRequestAdapter,
                           flask_support.FlaskResponseAdapter)


def init_flask(custom_formatter=None, enable_json=False):
    __init(framework_name='flask', custom_formatter=custom_formatter, enable_json=enable_json)


# register sanic support
# noinspection PyPep8
from json_logging.framework.sanic import SanicAppConfigurator, SanicAppRequestInstrumentationConfigurator, \
    SanicRequestAdapter, SanicResponseAdapter

register_framework_support('sanic', SanicAppConfigurator,
                           SanicAppRequestInstrumentationConfigurator,
                           SanicRequestAdapter,
                           SanicResponseAdapter)


def init_sanic(custom_formatter=None, enable_json=False):
    __init(framework_name='sanic', custom_formatter=custom_formatter, enable_json=enable_json)


# register quart support
# noinspection PyPep8
import json_logging.framework.quart as quart_support

register_framework_support('quart', None, quart_support.QuartAppRequestInstrumentationConfigurator,
                           quart_support.QuartRequestAdapter,
                           quart_support.QuartResponseAdapter)


def init_quart(custom_formatter=None, enable_json=False):
    __init(framework_name='quart', custom_formatter=custom_formatter, enable_json=enable_json)


# register connexion support
# noinspection PyPep8
import json_logging.framework.connexion as connexion_support

register_framework_support('connexion', None, connexion_support.ConnexionAppRequestInstrumentationConfigurator,
                           connexion_support.ConnexionRequestAdapter,
                           connexion_support.ConnexionResponseAdapter)


def init_connexion(custom_formatter=None, enable_json=False):
    __init(framework_name='connexion', custom_formatter=custom_formatter, enable_json=enable_json)


# register FastAPI support
import json_logging.framework.fastapi as fastapi_support

if fastapi_support.is_fastapi_present():
    register_framework_support('fastapi', app_configurator=None,
                               app_request_instrumentation_configurator=fastapi_support.FastAPIAppRequestInstrumentationConfigurator,
                               request_adapter_class=fastapi_support.FastAPIRequestAdapter,
                               response_adapter_class=fastapi_support.FastAPIResponseAdapter)


def init_fastapi(custom_formatter=None, enable_json=False):
    __init(framework_name='fastapi', custom_formatter=custom_formatter, enable_json=enable_json)
