import yaml

BASE_SCHEMA = {
    "name": {
        "required": True,
        "empty": False
    },
    "module": {
        "required": True,
        "empty": False
    },
    "cleanup": {
        "required": False,
        "type": "boolean",
        "default": True
    }
}

CONFIG_SCHEMA = yaml.load("""
mqtt:
  type: dict
  required: yes
  schema:
    host:
      type: string
      empty: no
      required: no
      default: localhost
    port:
      type: integer
      min: 1
      max: 65535
      required: no
      default: 1883
    user:
      type: string
      required: no
      default: ""
    password:
      type: string
      required: no
      default: ""
    client_id:
      type: string
      required: no
      default: ""
    topic_prefix:
      type: string
      required: no
      default: ""
      coerce: rstrip_slash
    protocol:
      type: string
      required: no
      empty: no
      coerce: tostring
      default: "3.1.1"
      allowed:
        - "3.1"
        - "3.1.1"
    status_topic:
      type: string
      required: no
      default: status
    status_payload_running:
      type: string
      required: no
      default: running
    status_payload_stopped:
      type: string
      required: no
      default: stopped
    status_payload_dead:
      type: string
      required: no
      default: dead
    tls:
      type: dict
      required: no
      schema:
        enabled:
          type: boolean
          required: yes
        ca_certs:
          type: string
          required: no
        certfile:
          type: string
          required: no
        keyfile:
          type: string
          required: no
        cert_reqs:
          type: string
          required: no
          allowed:
            - CERT_NONE
            - CERT_OPTIONAL
            - CERT_REQUIRED
          default: CERT_REQUIRED
        tls_version:
          type: string
          required: no
        ciphers:
          type: string
          required: no
        insecure:
          type: boolean
          required: no
          default: false

digital_outputs:
  type: list
  required: no
  default: []
  schema:
    type: dict
    schema:
      name:
        type: string
        required: yes
      on_payload:
        type: string
        required: no
        empty: no
      off_payload:
        type: string
        required: no
        empty: no
      endpoint:
        type: string
        required: yes
      retain:
        type: boolean
        required: no
        default: no

""")

