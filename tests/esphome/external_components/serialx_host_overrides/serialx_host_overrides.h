#pragma once

#ifdef USE_HOST

#include "esphome/components/uart/uart_component_host.h"
#include "esphome/core/component.h"

#include <string>

namespace esphome::serialx_host_overrides {

class SerialxHostOverridesComponent : public Component {
 public:
  void setup() override;
  void loop() override;
  float get_setup_priority() const override { return setup_priority::BUS + 1.0f; }

  void set_left_uart(uart::HostUartComponent *uart) { this->left_uart_ = uart; }
  void set_right_uart(uart::HostUartComponent *uart) { this->right_uart_ = uart; }
  void set_left_uart_env(std::string env_name) { this->left_uart_env_ = std::move(env_name); }
  void set_right_uart_env(std::string env_name) { this->right_uart_env_ = std::move(env_name); }
  void set_api_port_env(std::string env_name) { this->api_port_env_ = std::move(env_name); }
  void set_noise_psk_env(std::string env_name) { this->noise_psk_env_ = std::move(env_name); }

 protected:
  uart::HostUartComponent *left_uart_{nullptr};
  uart::HostUartComponent *right_uart_{nullptr};
  std::string left_uart_env_;
  std::string right_uart_env_;
  std::string api_port_env_;
  std::string noise_psk_env_;
  bool ready_printed_{false};
};

}  // namespace esphome::serialx_host_overrides

#endif  // USE_HOST
