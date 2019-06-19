import argparse
import logging
import yaml
import sys
import socket
import ssl
from time import sleep, time
from hashlib import sha1
from hashlib import md5

import paho.mqtt.client as mqtt
import cerberus

import re
import requests
from bs4 import BeautifulSoup

from scheduler import Scheduler, Task
from pi_mqtt_optoma import CONFIG_SCHEMA

LOG_LEVEL_MAP = {
    mqtt.MQTT_LOG_INFO: logging.INFO,
    mqtt.MQTT_LOG_NOTICE: logging.INFO,
    mqtt.MQTT_LOG_WARNING: logging.WARNING,
    mqtt.MQTT_LOG_ERR: logging.ERROR,
    mqtt.MQTT_LOG_DEBUG: logging.DEBUG
}
RECONNECT_DELAY_SECS = 5
LAST_STATES = {}
SET_TOPIC = "set"
SET_ON_MS_TOPIC = "set_on_ms"
SET_OFF_MS_TOPIC = "set_off_ms"
RESET_TOPIC = "reset"
OUTPUT_TOPIC = "output"
INPUT_TOPIC = "input"

CGI_REGEX = r".*pw:\"(.*?)\","

_LOG = logging.getLogger(__name__)
_LOG.addHandler(logging.StreamHandler())
_LOG.setLevel(logging.DEBUG)
logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


class CannotInstallModuleRequirements(Exception):
    pass


class InvalidPayload(Exception):
    pass

class ModuleConfigInvalid(Exception):
    def __init__(self, errors, *args, **kwargs):
        self.errors = errors
        super(ModuleConfigInvalid, self).__init__(*args, **kwargs)


class ConfigValidator(cerberus.Validator):
    """
    Cerberus Validator containing function(s) for use with validating or
    coercing values relevant to the pi_mqtt_gpio project.
    """

    @staticmethod
    def _normalize_coerce_rstrip_slash(value):
        """
        Strip forward slashes from the end of the string.
        :param value: String to strip forward slashes from
        :type value: str
        :return: String without forward slashes on the end
        :rtype: str
        """
        return value.rstrip("/")

    @staticmethod
    def _normalize_coerce_tostring(value):
        """
        Convert value to string.
        :param value: Value to convert
        :return: Value represented as a string.
        :rtype: str
        """
        return str(value)

def on_log(client, userdata, level, buf):
    """
    Called when MQTT client wishes to log something.
    :param client: MQTT client instance
    :param userdata: Any user data set in the client
    :param level: MQTT log level
    :param buf: The log message buffer
    :return: None
    :rtype: NoneType
    """
    _LOG.log(LOG_LEVEL_MAP[level], "MQTT client: %s" % buf)


def output_by_name(output_name):
    """
    Returns the output configuration for a given output name.
    :param output_name: The name of the output
    :type output_name: str
    :return: The output configuration or None if not found
    :rtype: dict
    """
    for output in digital_outputs:
        if output["name"] == output_name:
            return output
    _LOG.warning("No output found with name of %r", output_name)


def handle_set(msg):
    """
    Handles an incoming 'set' MQTT message.
    :param msg: The incoming MQTT message
    :type msg: paho.mqtt.client.MQTTMessage
    :return: None
    :rtype: NoneType
    """
    output_name = output_name_from_topic(msg.topic, topic_prefix, SET_TOPIC)
    output_config = output_by_name(output_name)
    if output_config is None:
        return
    payload = msg.payload.decode("utf8")
    if payload not in (
            output_config["on_payload"], output_config["off_payload"]):
        _LOG.warning(
            "Payload %r does not relate to configured on/off values %r, %r and %r",
            payload,
            output_config["on_payload"],
            output_config["off_payload"])
        return
    set_state(output_config, payload == output_config["on_payload"])

# Set status in digital input loop
    # client.publish(
    #     "%s/%s/%s" % (topic_prefix, OUTPUT_TOPIC, output_config["name"]),
    #     retain=output_config["retain"],
    #     payload=payload)


def handle_reset(msg):
    """
    Handles an incoming 'reset' MQTT message.
    :param msg: The incoming MQTT message
    :type msg: paho.mqtt.client.MQTTMessage
    :return: None
    :rtype: NoneType
    """
    output_name = output_name_from_topic(msg.topic, topic_prefix, SET_TOPIC)
    output_config = output_by_name(output_name)
    if output_config is None:
        return
    set_state(output_config, False)
    payload = msg.payload.decode("utf8")

# Set status in digital input loop
    # client.publish(
    #     "%s/%s/%s" % (topic_prefix, OUTPUT_TOPIC, output_config["name"]),
    #     retain=output_config["retain"],
    #     payload=payload)

def handle_set_ms(msg, value):
    """
    Handles an incoming 'set_<on/off>_ms' MQTT message.
    :param msg: The incoming MQTT message
    :type msg: paho.mqtt.client.MQTTMessage
    :param value: The value to set the output to
    :type value: bool
    :return: None
    :rtype: NoneType
    """
    try:
        ms = int(msg.payload)
    except ValueError:
        raise InvalidPayload(
            "Could not parse ms value %r to an integer." % msg.payload)
    suffix = SET_ON_MS_TOPIC if value else SET_OFF_MS_TOPIC
    output_name = output_name_from_topic(msg.topic, topic_prefix, suffix)
    output_config = output_by_name(output_name)
    if output_config is None:
        return

    set_state(output_config, value)
    scheduler.add_task(Task(
        time() + ms/1000.0,
        set_state,
        output_config,
        not value
    ))
    _LOG.info(
        "Scheduled output %r to change back to %r after %r ms.",
        output_config["name"],
        not value,
        ms
    )


def output_name_from_topic(topic, topic_prefix, suffix):
    """
    Return the name of the output which the topic is setting.
    :param topic: String such as 'mytopicprefix/output/tv_lamp/set'
    :type topic: str
    :param topic_prefix: Prefix of our topics
    :type topic_prefix: str
    :param suffix: The suffix of the topic such as "set" or "set_ms"
    :type suffix: str
    :return: Name of the output this topic is setting
    :rtype: str
    """
    if not topic.endswith("/%s" % suffix):
        raise ValueError("This topic does not end with '/%s'" % suffix)
    lindex = len("%s/%s/" % (topic_prefix, OUTPUT_TOPIC))
    rindex = -len(suffix)-1
    return topic[lindex:rindex]

def set_state(output_config, value):
    endpoint = output_config["endpoint"]
    _LOG.info("Set state to of %s %r.", endpoint, value)
    cookies = projector_login(output_config)
    set_projector_state(output_config, cookies, value)
    return

def projector_login(output_config):
    endpoint = output_config["endpoint"]
    # Get challenge
    r = requests.get("%s/login.htm" % endpoint)
    soup = BeautifulSoup(r.text, features="html.parser")

    # Calc response
    sleep(1)
    resp = "adminadmin" + soup.body.find(attrs={"name": "Challenge"})['value']
    m = md5()
    m.update(resp)
    payload = { 'user':'0', 'Username':'1', 'Response': m.hexdigest() }
    r = requests.post("%s/tgi/login.tgi" % endpoint, data=payload)
    cookies = r.cookies
    sleep(0.5)
    _LOG.info("Login to %s, cookies: %s, status code: %d", endpoint, cookies, r.status_code)
    return cookies

# on - u'{pw:"1",a:"1",b:"255",c:"0",d:"0",f:"0",t:"0",h:"0",j:"0",k:"0",l:"0",m:"6",n:"0",o:"1",p:"0",q:"0",r:"0",
# u:"20",v:"0",w:"0",x:"0",y:"0",z:"0",A:"0",B:"0",C:"255",D:"255",E:"0",H:"0",I:"0",K:"0",L:"255",M:"0",N:"0",O:"0",
# P:"0",Q:"0",R:"1",S:"f",T:"0",V:"0",W:"0",Y:"0",e:"0",g:"0",Z:"6"}'
# off -
def get_projector_state(output_config, cookies):
    endpoint = output_config["endpoint"]
    pw = ""

    try:
        state = request_get_state(endpoint, cookies)

    except AttributeError:
        # HTML payload, so login and try again
        _LOG.info("Get Projector response has no payload so login/retry")
        sleep(0.5)
        cookies = projector_login(output_config)
        state = request_get_state(endpoint, cookies)
    except Exception:
        _LOG.exception("Exception while /tgi/control.tgi")

    return {"state": state, "cookies": cookies}


def request_get_state(endpoint, cookies):
    r = requests.get("%s/tgi/control.tgi" % endpoint, cookies=cookies)
    m = re.match(CGI_REGEX, r.text)
    pw = m.group(1)

    _LOG.info("request_get_state %s: %s, cookies: %s, status code: %d", endpoint, pw, cookies, r.status_code)

    if pw == "0":
        state = False
    elif pw == "1":
        state = True
    else:
        _LOG.exception("Unknown state %s" % pw)

    return state

# off state:
    # u'{pw:"0",a:"1",b:"0",c:"1",d:"7",f:"8",t:"0",h:"0",j:"0",k:"0",l:"0",m:"6",n:"0",o:"1",p:"0",q:"0",r:"0",
# u:"20",v:"0",w:"0",x:"0",y:"0",z:"0",A:"0",B:"3",C:"0",D:"5",E:"0",H:"0",I:"0",K:"0",L:"5",M:"0",N:"2",O:"0",P:"0",
# Q:"0",R:"1",S:"f",T:"0",V:"0",W:"0",Y:"0",e:"0",g:"0",Z:"6"}'
# on state - may take few secs:
# u'{pw:"1",a:"1",b:"255",c:"0",d:"0",f:"0",t:"0",h:"0",j:"0",k:"0",l:"0",m:"6",n:"0",o:"1",p:"0",q:"0",r:"0",u:"20",
# v:"0",w:"0",x:"0",y:"0",z:"0",A:"0",B:"0",C:"255",D:"255",E:"0",H:"0",I:"0",K:"0",L:"255",M:"0",N:"0",O:"0",P:"0",
# Q:"0",R:"1",S:"f",T:"0",V:"0",W:"0",Y:"0",e:"0",g:"0",Z:"6"}'

def set_projector_state(output_config, cookies, value):
    endpoint = output_config["endpoint"]
    if value:
        payload = {'btn_powon': 'Power On'}
    else:
        payload = {'btn_powoff': 'Power Off'}

    try:
        pw = request_post_state(endpoint, payload, cookies)
    except AttributeError:
        # HTML payload, session expired
        sleep(0.5)
        _LOG.info("Set Projector response has no payload session expired")
        cookies = projector_login(output_config)
        pw = request_post_state(endpoint, payload, cookies)
    except Exception:
        _LOG.exception("Exception while /tgi/control.tgi")

    return pw

def request_post_state(endpoint, payload, cookies):
    r = requests.post("%s/tgi/control.tgi" % endpoint, data=payload, cookies=cookies)
    m = re.match(CGI_REGEX, r.text)
    pw = m.group(1)

    _LOG.info("request_post_state %s: %s: %s, cookies: %s, status code: %d", endpoint, payload, pw, cookies, r.status_code)

    return pw

def init_mqtt(config, digital_outputs):
    """
    Configure MQTT client.
    :param config: Validated config dict containing MQTT connection details
    :type config: dict
    :param digital_outputs: List of validated config dicts for digital outputs
    :type digital_outputs: list
    :return: Connected and initialised MQTT client
    :rtype: paho.mqtt.client.Client
    """
    topic_prefix = config["topic_prefix"]
    protocol = mqtt.MQTTv311
    if config["protocol"] == "3.1":
        protocol = mqtt.MQTTv31

    # https://stackoverflow.com/questions/45774538/what-is-the-maximum-length-of-client-id-in-mqtt
    # TLDR: Soft limit of 23, but we needn't truncate it on our end.
    client_id = config['client_id']
    if not client_id:
        client_id = "pi-mqtt-scrape-%s" % sha1(
            topic_prefix.encode('utf8')).hexdigest()

    client = mqtt.Client(
        client_id=client_id, clean_session=False, protocol=protocol)

    if config["user"] and config["password"]:
        client.username_pw_set(config["user"], config["password"])

    # Set last will and testament (LWT)
    status_topic = "%s/%s" % (topic_prefix, config["status_topic"])
    client.will_set(
        status_topic,
        payload=config["status_payload_dead"],
        qos=1,
        retain=True)
    _LOG.debug(
        "Last will set on %r as %r.",
        status_topic,
        config["status_payload_dead"])

    # Set TLS options
    tls_enabled = config.get("tls", {}).get("enabled")
    if tls_enabled:
        tls_config = config["tls"]
        tls_kwargs = dict(
            ca_certs=tls_config.get("ca_certs"),
            certfile=tls_config.get("certfile"),
            keyfile=tls_config.get("keyfile"),
            ciphers=tls_config.get("ciphers")
        )
        try:
            tls_kwargs["cert_reqs"] = getattr(ssl, tls_config["cert_reqs"])
        except KeyError:
            pass
        try:
            tls_kwargs["tls_version"] = getattr(ssl, tls_config["tls_version"])
        except KeyError:
            pass

        client.tls_set(**tls_kwargs)
        client.tls_insecure_set(tls_config["insecure"])

    def on_conn(client, userdata, flags, rc):
        """
        On connection to MQTT, subscribe to the relevant topics.
        :param client: Connected MQTT client instance
        :type client: paho.mqtt.client.Client
        :param userdata: User data
        :param flags: Response flags from the broker
        :type flags: dict
        :param rc: Response code from the broker
        :type rc: int
        :return: None
        :rtype: NoneType
        """
        if rc == 0:
            _LOG.info(
                "Connected to the MQTT broker with protocol v%s.",
                config["protocol"])
            for out_conf in digital_outputs:
                for suffix in (SET_TOPIC, RESET_TOPIC, SET_ON_MS_TOPIC, SET_OFF_MS_TOPIC):
                    topic = "%s/%s/%s/%s" % (
                        topic_prefix,
                        OUTPUT_TOPIC,
                        out_conf["name"],
                        suffix)
                    client.subscribe(topic, qos=1)
                    _LOG.info("Subscribed to topic: %r", topic)
            client.publish(
                status_topic,
                config["status_payload_running"],
                qos=1,
                retain=True)
        elif rc == 1:
            _LOG.fatal(
                "Incorrect protocol version used to connect to MQTT broker.")
            sys.exit(1)
        elif rc == 2:
            _LOG.fatal(
                "Invalid client identifier used to connect to MQTT broker.")
            sys.exit(1)
        elif rc == 3:
            _LOG.warning("MQTT broker unavailable. Retrying in %s secs...")
            sleep(RECONNECT_DELAY_SECS)
            client.reconnect()
        elif rc == 4:
            _LOG.fatal(
                "Bad username or password used to connect to MQTT broker.")
            sys.exit(1)
        elif rc == 5:
            _LOG.fatal(
                "Not authorised to connect to MQTT broker.")
            sys.exit(1)

    def on_msg(client, userdata, msg):
        """
        On reception of MQTT message, set the relevant output to a new value.
        :param client: Connected MQTT client instance
        :type client: paho.mqtt.client.Client
        :param userdata: User data (any data type)
        :param msg: Received message instance
        :type msg: paho.mqtt.client.MQTTMessage
        :return: None
        :rtype: NoneType
        """
        try:
            _LOG.info(
                "Received message on topic %r: %r", msg.topic, msg.payload)
            if msg.topic.endswith("/%s" % SET_TOPIC):
                handle_set(msg)
            elif msg.topic.endswith("/%s" % RESET_TOPIC):
                handle_reset(msg)
            elif msg.topic.endswith("/%s" % SET_ON_MS_TOPIC):
                handle_set_ms(msg, True)
            elif msg.topic.endswith("/%s" % SET_OFF_MS_TOPIC):
                handle_set_ms(msg, False)
            else:
                _LOG.warning("Unhandled topic %r.", msg.topic)
        except InvalidPayload as exc:
            _LOG.warning("Invalid payload on received MQTT message: %s" % exc)
        except Exception:
            _LOG.exception("Exception while handling received MQTT message:")

    client.on_connect = on_conn
    client.on_message = on_msg
    client.on_log = on_log

    return client


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("config")
    args = p.parse_args()

    with open(args.config) as f:
        config = yaml.load(f)
    validator = ConfigValidator(CONFIG_SCHEMA)
    if not validator.validate(config):
        _LOG.error(
            "Config did not validate:\n%s",
            yaml.dump(validator.errors))
        sys.exit(1)
    config = validator.normalized(config)

    digital_outputs = config["digital_outputs"]

    client = init_mqtt(config["mqtt"], config["digital_outputs"])

    try:
        client.connect(config["mqtt"]["host"], config["mqtt"]["port"], 60)
    except socket.error as err:
        _LOG.fatal("Unable to connect to MQTT server: %s" % err)
        sys.exit(1)
    client.loop_start()

    scheduler = Scheduler()

    topic_prefix = config["mqtt"]["topic_prefix"]
    cookies = {}
    for out_conf in digital_outputs:
        cookies[out_conf["name"]] = None
        LAST_STATES[out_conf["name"]] = None

    try:
        while True:
            for out_conf in digital_outputs:
                # Only login if we have no cookies
                name = out_conf["name"]
                if cookies[name] is None:
                    cookies[name] = projector_login(out_conf)

                # Get latest state from projector
                try:
                    response = get_projector_state(out_conf, cookies[name])
                    cookies[name] = response["cookies"]
                    state = response["state"]
                except Exception:
                    _LOG.exception("Error getting projector state")

                # Update status if different
                if state != LAST_STATES[name]:
                    _LOG.info(
                        "Output %r state changed to %r",
                        name,
                        state)
                    client.publish(
                        "%s/%s/%s" % (
                            topic_prefix, OUTPUT_TOPIC, name
                        ),
                        payload=(out_conf["on_payload"] if state
                                 else out_conf["off_payload"]),
                        retain=out_conf["retain"]
                    )
                LAST_STATES[name] = state
            scheduler.loop()
            sleep(5)
    except KeyboardInterrupt:
        print("")
    finally:
        client.publish(
            "%s/%s" % (topic_prefix, config["mqtt"]["status_topic"]),
            config["mqtt"]["status_payload_stopped"], qos=1, retain=True)

        client.loop_stop()
        client.disconnect()
        client.loop_forever()

