from vmngclient.session import Session
import logging
from abc import ABC, abstractmethod
from typing import cast,Union,List

from tenacity import retry, retry_if_result, stop_after_attempt, wait_fixed

from vmngclient.api.basic_api import DevicesAPI, DeviceStateAPI
from vmngclient.dataclasses import Device
from vmngclient.session import Session
from vmngclient.utils.certificate_status import CertificateStatus
from vmngclient.utils.creation_tools import get_logger_name
from vmngclient.utils.reachability import Reachability
from vmngclient.utils.validate_status import ValidateStatus

class ActionAPI():
    """API methods to execute action and verify its status """

    def __init__(self, session: Session, dev: Device):
        self.session = session
        self.action_status = ''
        self.action_id = ''

    def __str__(self):
        return str(self.session)

    def wait_for_completed(self, 
        sleep_seconds: int,
        timeout_seconds: int,
        exit_statuses : Union[List(str),str],
        exit_statuses_ids : Union[List(str),str],
        action_id : str,
        activity_text : str = None,
        action_url: str = '/dataservice/device/action/status/',
        
         ) -> None:
        
        def check_status(status: str, status_id: str, activity: str):
            if status in exit_statuses and status_id in exit_statuses_ids:
                if activity_text:
                    if activity_text == activity:
                        return False
                    else:
                        return True
                return False
                    

        @retry(
            wait=wait_fixed(sleep_seconds),
            stop=stop_after_attempt(int(timeout_seconds / sleep_seconds)),
            retry=retry_if_result(check_status),
        )
        def wait_for_action_finish():
            url = f'{action_url}{action_id}'
            try:
                action_data = self.session.get_data(url)[0]
                status = action_data['status']
                status_id = action_data['statusId']
                activity = action_data['activity'][0]
                logger.debug(f"Statuses of action {action_id} is: \
                     status: {status}, status_id: {status_id}, activity: {activity} ")
            except IndexError:
                action_data = ''
            
            if status in exit_statuses and status_id in exit_statuses_ids:
                if activity_text:
                    if activity_text == activity:
                        return True
                    else:
                        return False
                return True

        wait_for_action_finish()