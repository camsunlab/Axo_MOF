import json
import os
from typing import Tuple, Union

from science_jubilee.labware.Labware import Labware, Location, Well
from science_jubilee.tools.Tool import (
    Tool,
    ToolConfigurationError,
    ToolStateError,
    requires_active_tool
)

class VacuumGripper(Tool):
    def __init__(
        self,
        index: int,
        name: str,
        vacuum_pin : int = None,
        limit_switch_pin : int = None
    ):
        super().__init__(index, name, vacuum_pin=vacuum_pin)
        if vacuum_pin is None:
            raise ToolConfigurationError("VacuumGripper requires a vacuum_pin to be specified")
        if limit_switch_pin is None:
            raise ToolConfigurationError("VacuumGripper requires a limit_switch_pin to be specified")
        
        self.vacuum_pin = vacuum_pin
        self.limit_switch_pin = limit_switch_pin
        
    @requires_active_tool
    def grip(self, 
             dict_location : dict, 
             pwm : int,
             retract_z_after_probe : float = 3.0):
        
        assert 0 <= pwm <= 1 

        # Add a error handling if the given grip location has lid or not
        labwares_list = dict_location["labwares_list"]
        for labware in labwares_list:
            self.lid_on_top_error_handling(location= labware, expected_condition = True)



        location = dict_location["loc"]
        
        x, y, z = Labware._getxyz(location)
        
        self._machine.safe_z_movement()
        self._machine.move_to(x = x, y = y, wait = True)
        
        # # Activate vacuum before probing
        self._machine.gcode(f"M42 P{self.vacuum_pin} S{pwm}")
        
        # Trigger probing to Z-stop attached to gripper, S-1 to avoid z-offset changes 
        self._probe_limit_switch(retract_z_after_probe)

        # Reset the has_lid_on_top attribute to False
        for labware in labwares_list:
            self.revert_lid_on_top(location= labware)

    @requires_active_tool 
    def drop(self, 
             dict_location : dict,
             retract_z_after_probe : float = 0.0):
        
        # Add a error handling if the given grip location has lid or not
        labwares_list = dict_location["labwares_list"]
        for labware in labwares_list:
            self.lid_on_top_error_handling(location= labware, expected_condition = False)
        
        # Get the location from the dictionary
        location = dict_location["loc"]

        x, y, z = Labware._getxyz(location)
        
        self._machine.safe_z_movement()
        self._machine.move_to(x = x, y = y, wait = True)
        self._probe_limit_switch(retract_z_after_probe)
        
        self._machine.gcode(f"M42 P{self.vacuum_pin} S0")

        # Reset the has_lid_on_top attribute to True
        for labware in labwares_list:
            self.revert_lid_on_top(location= labware)



    def _probe_limit_switch(self, retract_z_after_probe : float = 0.0):
        """Triggers limit switch probing and retracts Z afterwards."""
        self._machine.gcode(f"G30 K{self.limit_switch_pin} S-1")
        if retract_z_after_probe > 0:
            self._machine.move(dz=retract_z_after_probe, wait=True)
       
    @requires_active_tool
    def pick_and_place(self, 
                       dict_grip : dict, 
                       dict_drop : dict,
                       pwm,
                       retract_z_after_probe):
        
        self.grip(dict_grip, pwm, retract_z_after_probe)
        self.drop(dict_drop)
        self._machine.safe_z_movement()
        