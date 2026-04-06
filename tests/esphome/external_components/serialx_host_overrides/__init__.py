"""Host-only runtime overrides for serialx ESPHome daemon test fixtures."""

import esphome.codegen as cg
from esphome.components import uart
import esphome.config_validation as cv
from esphome.const import CONF_ID, PLATFORM_HOST

CODEOWNERS = ["@puddly"]
DEPENDENCIES = ["api", "uart"]

CONF_API_PORT_ENV = "api_port_env"
CONF_NOISE_PSK_ENV = "noise_psk_env"
CONF_LEFT_UART_ENV = "left_uart_env"
CONF_LEFT_UART_ID = "left_uart_id"
CONF_RIGHT_UART_ENV = "right_uart_env"
CONF_RIGHT_UART_ID = "right_uart_id"

DEFAULT_API_PORT_ENV = "SERIALX_API_PORT"
DEFAULT_NOISE_PSK_ENV = "SERIALX_NOISE_PSK"
DEFAULT_LEFT_UART_ENV = "SERIALX_UART_LEFT"
DEFAULT_RIGHT_UART_ENV = "SERIALX_UART_RIGHT"

serialx_host_overrides_ns = cg.esphome_ns.namespace("serialx_host_overrides")
SerialxHostOverridesComponent = serialx_host_overrides_ns.class_(
    "SerialxHostOverridesComponent",
    cg.Component,
)

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(SerialxHostOverridesComponent),
            cv.Required(CONF_LEFT_UART_ID): cv.use_id(uart.HostUartComponent),
            cv.Required(CONF_RIGHT_UART_ID): cv.use_id(uart.HostUartComponent),
            cv.Optional(
                CONF_LEFT_UART_ENV, default=DEFAULT_LEFT_UART_ENV
            ): cv.string_strict,
            cv.Optional(
                CONF_RIGHT_UART_ENV, default=DEFAULT_RIGHT_UART_ENV
            ): cv.string_strict,
            cv.Optional(
                CONF_API_PORT_ENV, default=DEFAULT_API_PORT_ENV
            ): cv.string_strict,
            cv.Optional(
                CONF_NOISE_PSK_ENV, default=DEFAULT_NOISE_PSK_ENV
            ): cv.string_strict,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on(PLATFORM_HOST),
)


async def to_code(config):
    """Generate host runtime override component wiring."""
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    left_uart = await cg.get_variable(config[CONF_LEFT_UART_ID])
    right_uart = await cg.get_variable(config[CONF_RIGHT_UART_ID])

    cg.add(var.set_left_uart(left_uart))
    cg.add(var.set_right_uart(right_uart))
    cg.add(var.set_left_uart_env(config[CONF_LEFT_UART_ENV]))
    cg.add(var.set_right_uart_env(config[CONF_RIGHT_UART_ENV]))
    cg.add(var.set_api_port_env(config[CONF_API_PORT_ENV]))
    cg.add(var.set_noise_psk_env(config[CONF_NOISE_PSK_ENV]))
