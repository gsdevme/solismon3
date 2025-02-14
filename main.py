# ver. 0.0.29
import logging
import traceback
from json import dumps
from sys import exit
from time import strptime, mktime, sleep

import paho.mqtt.client as mqtt
from environs import Env
from prometheus_client import start_http_server
from prometheus_client.core import GaugeMetricFamily, REGISTRY
from pysolarmanv5.pysolarmanv5 import PySolarmanV5

import config.registers as registers

metrics_dict = {}
env = Env()


def add_modified_metrics(custom_metrics_dict):
    met_pwr = custom_metrics_dict['meter_active_power_1'] - custom_metrics_dict['meter_active_power_2']
    total_load = custom_metrics_dict['house_load_power'] + custom_metrics_dict['bypass_load_power']

    # Present battery modified metrics
    if custom_metrics_dict['battery_current_direction'] == 0:
        metrics_dict['battery_power_modified'] = 'Battery Power(modified)', custom_metrics_dict['battery_power_2']
        metrics_dict['battery_power_in_modified'] = 'Battery Power In(modified)', custom_metrics_dict['battery_power_2']
        metrics_dict['battery_power_out_modified'] = 'Battery Power Out(modified)', 0
        metrics_dict['grid_to_battery_power_in_modified'] = 'Grid to Battery Power In(modified)', 0
    else:
        metrics_dict['battery_power_modified'] = 'Battery Power(modified)', custom_metrics_dict[
            'battery_power_2'] * -1  # negative
        metrics_dict['battery_power_out_modified'] = 'Battery Power Out(modified)', custom_metrics_dict[
            'battery_power_2']
        metrics_dict['battery_power_in_modified'] = 'Battery Power In(modified)', 0
        metrics_dict['grid_to_battery_power_in_modified'] = 'Grid to Battery Power In(modified)', 0

    if total_load < met_pwr and custom_metrics_dict['battery_power_2'] > 0:
        metrics_dict['grid_to_battery_power_in_modified'] = 'Grid to Battery Power In(modified)', custom_metrics_dict[
            'battery_power_2']

    # Present meter modified metrics
    if met_pwr > 0:
        metrics_dict['meter_power_in_modified'] = 'Meter Power In(modified)', met_pwr
        metrics_dict['meter_power_modified'] = 'Meter Power(modified)', met_pwr
        metrics_dict['meter_power_out_modified'] = 'Meter Power Out(modified)', 0
    else:
        metrics_dict['meter_power_out_modified'] = 'Meter Power Out(modified)', met_pwr * - 1  # negative
        metrics_dict['meter_power_in_modified'] = 'Meter Power In(modified)', 0
        metrics_dict['meter_power_modified'] = 'Meter Power(modified)', met_pwr

    # Present load modified metrics
    metrics_dict['total_load_power_modified'] = 'Total Load Power(modified)', total_load

    if 0 < custom_metrics_dict['total_dc_input_power_2'] <= total_load:
        metrics_dict['solar_to_house_power_modified'] = 'Solar To House Power(modified)', custom_metrics_dict[
            'total_dc_input_power_2']
    elif custom_metrics_dict['total_dc_input_power_2'] == 0:
        metrics_dict['solar_to_house_power_modified'] = 'Solar To House Power(modified)', 0
    elif custom_metrics_dict['total_dc_input_power_2'] > total_load:
        metrics_dict['solar_to_house_power_modified'] = 'Solar To House Power(modified)', total_load

    logging.info('Added modified metrics')


def scrape_solis():
    custom_metrics_dict = {}
    global metrics_dict
    metrics_dict = {}
    regs_ignored = 0
    try:
        logging.info('Connecting to Solis Modbus')
        modbus = PySolarmanV5(
            env.str("INVERTER_IP"), env.int("INVERTER_SERIAL"), port=env.int("INVERTER_PORT"),
            mb_slave_id=1, verbose=env.bool("DEBUG"), socket_timeout=env.int("INVERTER_SOCKET_TIMEOUT"))
    except Exception as e:
        e.add_note('Connection to Solis modbus failed')
        raise e

    logging.info('Scraping...')

    for r in registers.all_regs:
        reg = r[0]
        reg_len = len(r[1])
        reg_des = r[1]

        # Sometimes the query fails this will retry 3 times before exiting
        c = 0
        while True:
            try:
                logging.debug(f'Scrapping registers {reg} length {reg_len}')
                # read registers at address , store result in regs list
                regs = modbus.read_input_registers(register_addr=reg, quantity=reg_len)
                logging.debug(regs)
            except Exception as e:
                if c == 3:
                    e.add_note(f'Cannot read registers {reg} length{reg_len}. Tried {c} times. Exiting {repr(e)}')
                    raise e
                else:
                    c += 1
                    logging.info(f'Cannot read registers {reg} length {reg_len} {repr(e)}')
                    logging.info(f'Retry {c} in 5s')
                    sleep(5)  # hold before retry
                    continue
            break

        # Convert time to epoch
        if reg == 33022:
            inv_year = '20' + str(regs[0]) + '-'
            if regs[1] < 10:
                inv_month = '0' + str(regs[1]) + '-'
            else:
                inv_month = str(regs[1]) + '-'
            if regs[2] < 10:
                inv_day = '0' + str(regs[2]) + ' '
            else:
                inv_day = str(regs[2]) + ' '
            if regs[3] < 10:
                inv_hour = '0' + str(regs[3]) + ':'
            else:
                inv_hour = str(regs[3]) + ':'
            if regs[4] < 10:
                inv_min = '0' + str(regs[4]) + ':'
            else:
                inv_min = str(regs[4]) + ':'
            if regs[5] < 10:
                inv_sec = '0' + str(regs[5])
            else:
                inv_sec = str(regs[5])
            inv_time = inv_year + inv_month + inv_day + inv_hour + inv_min + inv_sec
            logging.info(f'Solis Inverter time: {inv_time}')
            time_tuple = strptime(inv_time, '%Y-%m-%d %H:%M:%S')
            time_epoch = mktime(time_tuple)
            metrics_dict['system_epoch'] = 'System Epoch Time', time_epoch

        # Add metric to list

        for (i, item) in enumerate(regs):
            if '*' not in reg_des[i][0]:
                metrics_dict[reg_des[i][0]] = reg_des[i][1], item

                # Add custom metrics to custom_metrics_dict
                # Get battery metric for modification
                if reg_des[i][0] == 'battery_power_2':
                    custom_metrics_dict[reg_des[i][0]] = item
                elif reg_des[i][0] == 'battery_current_direction':
                    custom_metrics_dict[reg_des[i][0]] = item

                # Get grid metric for modification
                elif reg_des[i][0] == 'meter_active_power_1':
                    custom_metrics_dict[reg_des[i][0]] = item
                elif reg_des[i][0] == 'meter_active_power_2':
                    custom_metrics_dict[reg_des[i][0]] = item

                # Get load metric for modification
                elif reg_des[i][0] == 'house_load_power':
                    custom_metrics_dict[reg_des[i][0]] = item
                elif reg_des[i][0] == 'total_dc_input_power_2':
                    custom_metrics_dict[reg_des[i][0]] = item
                elif reg_des[i][0] == 'bypass_load_power':
                    custom_metrics_dict[reg_des[i][0]] = item

            else:
                regs_ignored += 1

    logging.info(f'Ignored registers: {regs_ignored}')

    # Create modified metrics
    if env.bool("MODIFIED_METRICS"):
        add_modified_metrics(custom_metrics_dict)
    logging.info('Scraped')


def publish_mqtt():
    mqtt_dict = {}

    if not env.bool("PROMETHEUS"):
        scrape_solis()

    try:
        # Resize dictionary and convert to JSON
        for metric, value in metrics_dict.items():
            mqtt_dict[metric] = value[1]
        mqtt_json = dumps(mqtt_dict)

        def on_message(mqttc, userdata, msg):
            print(f"Message received [{msg.topic}]: {msg.payload}")

        mqttc = mqtt.Client()
        if env.str("MQTT_USER") != '':
            mqttc.username_pw_set(env.str("MQTT_USER"), env.str("MQTT_PASS"))
        mqttc.connect(env.str("MQTT_SERVER"), env.int("MQTT_PORT"), env.int("MQTT_KEEPALIVE"))
        mqttc.on_connect = logging.info(
            f'Connected to MQTT {env.str("MQTT_SERVER")}:{env.int("MQTT_PORT")}')

        logging.info('Publishing MQTT')
        mqttc.publish(topic=env.str("MQTT_TOPIC"), payload=mqtt_json)

        mqttc.disconnect()

    except Exception as e:
        e.add_note('Could not connect to MWTT')
        raise e

class CustomCollector(object):
    def __init__(self):
        pass

    def collect(self):
        scrape_solis()
        publish_mqtt()

        for metric, value in metrics_dict.items():
            yield GaugeMetricFamily(metric, value[0], value=value[1])


if __name__ == '__main__':
    env.read_env()

    try:
        if env.bool("DEBUG"):
            logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.DEBUG,
                                datefmt='%Y-%m-%d %H:%M:%S')
        else:
            logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO,
                                datefmt='%Y-%m-%d %H:%M:%S')
        logging.info('Starting')

        if env.bool("PROMETHEUS"):
            logging.info(f'Starting Web Server for Prometheus on port: {env.str("PROMETHEUS_PORT")}')
            start_http_server(int(env.str("PROMETHEUS_PORT")))

            REGISTRY.register(CustomCollector())
            while True:
                sleep(env.int("CHECK_INTERVAL"))

        else:
            while True:
                publish_mqtt()
                sleep(env.int("CHECK_INTERVAL"))

    except Exception as e:
        logging.error(repr(e))

        if env.bool("DEBUG"):
            print(traceback.format_exc())

        exit(1)
