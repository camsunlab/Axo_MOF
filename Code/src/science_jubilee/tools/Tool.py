from typing import Dict, Iterable, List, NamedTuple, Tuple, Union
from science_jubilee.labware.Labware import Labware, Location, Well

class ToolStateError(Exception):
    """Raise this error if the tool is in the wrong state to perform such a command."""

    pass


class ToolConfigurationError(Exception):
    """Raise this error if there is something wrong with how the tool is configured"""

    pass


class Tool:
    # TODO: Is this init supposed to take a machine?
    def __init__(self, index, name, **kwargs):
        if not isinstance(index, int) or not isinstance(name, str):
            raise ToolConfigurationError(
                "Incorrect usage: load_tool(<tool_number>, <name>, **kwargs)"
            )
        self._machine = None
        self.index = index
        self.name = name
        self.is_active_tool = False
        self.tool_offset = None

        for k, v in kwargs.items():
            setattr(self, k, v)

    def post_load(self):
        """Run any code after tool has been associated with the machine."""
        pass

    # TODO:
    # add a park tool method that every tool config can define to do things that need to be done pre or post parking
    # ex: make sure pipette has dropped tips before parking 


    class OverridableError(Exception):
        """ Custom exception that can be overridden by the user"""
        def __init__(self, message):
            self.message = message
            super().__init__(self.message)

        def ask_override(self):
            """Ask the user if they want to override"""
            print(f"Error : {self.message}")
            choice = input("Do you want to override? (y/n): ").lower().strip()
            bool_choice = choice in ['y', 'yes']
            return bool_choice

    def lid_on_top_error_handling(self, location: Union[Well, Tuple, Location, Labware], expected_condition: bool):
        """Raise an error if the lid is on top of the tool.""" 

        # Error handling to check if the labware at location has lid or not
        if isinstance(location, Well):
            labware = location.labware_obj
        elif isinstance(location, Location):
            well_obj = location._labware
            labware = well_obj.labware_obj 
        elif isinstance(location, Labware):
            labware = location

        bool_override_choice = True 

        if labware.has_lid_on_top == expected_condition:
            pass
        else: 
            if labware.has_lid_on_top == True:
                error = self.OverridableError(f"Lid is on top of {labware}")
                # raise ToolStateError(f"{labware} Labware has lid on top")  
                bool_override_choice = error.ask_override()      
            else:
                error = self.OverridableError(f"Lid is not on top of {labware}")
                # raise ToolStateError(f"{labware} Labware has no lid on top")  
                bool_override_choice = error.ask_override()    

            
        if bool_override_choice:
            pass
        else:
            raise error

    def revert_lid_on_top(self, location: Union[Well, Tuple, Location, Labware]):
        """Revert the lid on top of the tool."""
        if isinstance(location, Well):
            labware = location.labware_obj
        elif isinstance(location, Location):
            well_obj = location._labware
            labware = well_obj.labware_obj 
        elif isinstance(location, Labware):
            labware = location
        
        labware.has_lid_on_top = not labware.has_lid_on_top

    def check_liquid_level(self, location: Union[Well, Tuple, Location], target_volume: float, is_dispense :bool):
        """ Error handling function to check if the well at location has enough liquid to aspirate target_volume 
            Or if can accomdate target_volume for dispensing at the well location
        """

        # Get the well object from the location
        if isinstance(location, Well):
            well_obj = location
        elif isinstance(location, Location):
            well_obj = location._labware

        bool_override_choice = True

        # Check if the well has enough liquid to aspirate or dispense the target_volume
        if is_dispense == True:                    # volume is to be dispensed at the location
            if well_obj.currentLiquidVolume + target_volume <= well_obj.totalLiquidVolume:
                pass 
            else: 
                error = self.OverridableError(f"{well_obj} Well cannot accomodate {target_volume} ml dispense liquid volume ")
                # raise ToolStateError(f"{well_obj} Well cannot accomodate {target_volume} ml dispense liquid volume ") 
                bool_override_choice = error.ask_override()

        else:                                      # volume is to be aspirated out of the location
            if well_obj.currentLiquidVolume - target_volume >= 0:   
                pass
            else: 
                error = self.OverridableError(f"{well_obj} Well does not have enough liquid to aspirate {target_volume} ml liquid volume ")
                # raise ToolStateError(f"{well_obj} Well does not have enough liquid to aspirate {target_volume} ml liquid volume ") 
                bool_override_choice = error.ask_override()
        


        if bool_override_choice:
            # if the user overrides the error message and it is an aspirate operation, then we need to reset the currentLiquidVolume of the Well
            if is_dispense == False: 
                well_obj.currentLiquidVolume = well_obj.totalLiquidVolume
        else:
            raise error

    def update_currentLiquidVolume(self, volume: float, location: Union[Well, Tuple, Location], is_dispense: bool):
        """Update the current liquid volume for the well at location.""" 

        # Get the well object from the location
        if isinstance(location, Well):
            well_obj = location
        elif isinstance(location, Location):
            well_obj = location._labware 
        
        # Update the current liquid volume for the well
        if is_dispense == True:                    # volume is dispensed at the location 
            well_obj.currentLiquidVolume += volume
        else:
            well_obj.currentLiquidVolume -= volume

def requires_active_tool(func):
    """Decorator to ensure that a tool cannot complete an action unless it is the
    current active tool.
    """

    def wrapper(self, *args, **kwargs):
        if self.is_active_tool == False:
            raise ToolStateError(
                f"Error: Tool {self.name} is not the current `Active Tool`. Cannot perform this action"
            )
        else:
            return func(self, *args, **kwargs)

    return wrapper
