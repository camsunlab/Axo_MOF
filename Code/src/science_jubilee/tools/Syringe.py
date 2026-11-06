import json
import os
import warnings
from typing import Tuple, Union

import numpy as np

from science_jubilee.labware.Labware import Labware, Location, Well
from science_jubilee.tools.Tool import (
    Tool,
    ToolConfigurationError,
    ToolStateError,
    requires_active_tool,
)


class Syringe(Tool):
    """A class representation of a syringe.

    :param Tool: The base tool class
    :type Tool: :class:`Tool`
    """

    def __init__(self, index, name, config):
        """Constructor method"""
        super().__init__(index, name)

        self.min_range = 0
        self.max_range = None
        self.mm_to_ml = None
        self.e_drive = "E2"

        self.load_config(config)

    def load_config(self, config):
        """Loads the confirguration file for the syringe tool

        :param config: Name of the config file for your syringe. Expects the file to be in /tools/configs
        :type config: str
        """

        config_directory = os.path.join(os.path.dirname(__file__), "configs")
        config_path = os.path.join(config_directory, f"{config}.json")
        if not os.path.isfile(config_path):
            raise ToolConfigurationError(
                f"Error: Config file {config_path} does not exist!"
            )

        with open(config_path, "r") as f:
            config = json.load(f)
        self.min_range = config["min_range"]
        self.max_range = config["max_range"]
        self.mm_to_ml = config["mm_to_ml"]

        # Check that all information was provided
        if None in vars(self):
            raise ToolConfigurationError(
                "Error: Not enough information provided in configuration file."
            )

    def post_load(self):
        """Query the object model after loading the tool to find the extruder number of this syringe."""

        # To read the position of an extruder, we need to know which extruder number to look at
        # Query the object model to find this
        tool_info = json.loads(self._machine.gcode('M409 K"tools[]"'))["result"]
        for tool in tool_info:
            if tool["number"] == self.index:
                self.e_drive = (
                    f"E{tool['extruders'][0]}"  # Syringe tool has only 1 extruder
                )
            else:
                continue

    def check_bounds(self, pos):
        """Disallow commands outside of the syringe's configured range

        :param pos: The E position to check
        :type pos: float
        """

        if pos > self.max_range or pos < self.min_range:
            raise ToolStateError(f"Error: {pos} is out of bounds for the syringe!")


    @requires_active_tool
    def _aspirate(self, vol: float, location: Union[Well, Tuple, Location], s: int = 50):
        """Aspirate a certain volume in milliliters. Used only to move the syringe; to aspirate from a particular well, see aspirate()
 
        :param vol: Volume to aspirate, in milliliters
        :type vol: float
        :param s: Speed at which to aspirate in mm/min, defaults to 2000
        :type s: int, optional
        """
        # Error handling to check if the well at loc has enough liquid or not(For aspirate only) 
        # and to check if the target dispense volume does not overfill the well (For dispense only)
        self.check_liquid_level(location=location, target_volume=vol, is_dispense=False)

        travel_mm = vol * self.mm_to_ml

        current_pos = float(self._machine.get_position()[self.e_drive])
        end_pos = current_pos + travel_mm

        # aspirate: if we can't move any further, syringe is full
        headroom_mm = self.max_range - current_pos
        actual_mm = min(travel_mm, headroom_mm)
        if actual_mm <= 0:
            # already at max_range
            return "Syringe already full; no aspirate performed"
        
        end_pos = current_pos + actual_mm
        self.check_bounds(end_pos)

        self._machine.move(de0=actual_mm, s=s, wait=True)

        # Calculate Aspirated Volume
        Aspirated_Volume = actual_mm / self.mm_to_ml

        # Update the currentLiquidVolume of the well at location 
        self.update_currentLiquidVolume(volume=Aspirated_Volume, location=location, is_dispense=False)
        
        # Return Aspirated Volume     
        return Aspirated_Volume

    @requires_active_tool
    def aspirate(
        self, vol: float, location: Union[Well, Tuple, Location], s: int = 50
    ):
        """Aspirate a certain volume from a given well.

        :param vol: Volume to aspirate, in milliliters
        :type vol: float
        :param location: The location (e.g. a `Well` object) from where to aspirate the liquid from.
        :type location: Union[Well, Tuple, Location]
        :param s: Speed at which to aspirate in mm/min, defaults to 2000
        :type s: int, optional
        """ 

        # Error handling to check if the labware at location has lid or not
        self.lid_on_top_error_handling(location= location, expected_condition = False)
        
        x, y, z = Labware._getxyz(location)

        self._machine.safe_z_movement()
        self._machine.move_to(x=x, y=y)
        self._machine.move_to(z=z)

        self._aspirate(vol, location, s=s)

    @requires_active_tool
    def _dispense(self, vol, sample_loc: Union[Well, Tuple, Location], refill_loc: Union[Well, Tuple, Location] = None, s: int = 50):
        """Dispense a certain volume in milliliters. Used only to move the syringe; to dispense into a particular well, see dispense()

        :param vol: Volume to dispense, in milliliters
        :type vol: float
        :param sample_loc: The location (e.g. a `Well` object) from where to aspirate the liquid from.
        :type sample_loc: Union[Well, Tuple, Location]
        :param refill_loc: The location (e.g. a `Well` object) from where to aspirate the liquid from.
        :type refill_loc: Union[Well, Tuple, Location]
        :param s: Speed at which to dispense in mm/min, defaults to 2000
        :type s: int, optional
        """

        #  Error handling to check if the labware at location has lid or not
        self.lid_on_top_error_handling(location= sample_loc, expected_condition = False)

        # Error handling to check if the well at loc has enough liquid or not(For aspirate only) 
        # and to check if the target dispense volume does not overfill the well (For dispense only)
        self.check_liquid_level(location=sample_loc, target_volume=vol, is_dispense=True)


        travel_mm = vol * -1 * self.mm_to_ml
        current_pos = self._machine.get_position() 

        # This is to account for the rounding errors which makes travel_mm slightly more negative than the current_pos sometimes
        if (travel_mm > -float(current_pos[self.e_drive])-0.2) and (travel_mm < -float(current_pos[self.e_drive])):
            travel_mm = -float(current_pos[self.e_drive])

        end_pos = float(current_pos[self.e_drive]) + travel_mm 

        if end_pos < self.min_range: # dispense underflows

            if refill_loc is None:
                raise ToolStateError(f"dispense below 0 without refill location.")
            # aspirate from refill_loc
            self.refill(refill_loc=refill_loc, s = 150)

        # return to the original well coords
        x, y, z = Labware._getxyz(sample_loc)

        self._machine.safe_z_movement()
        self._machine.move_to(x=x, y=y, wait= True)
        self._machine.move_to(z=z, wait= True)
            
        # recompute pos now at full, end_pos = full + de
        refilled_pos = float(self._machine.get_position()[self.e_drive])
        end_pos = refilled_pos + travel_mm

        if end_pos < self.min_range:
            raise ToolStateError(f"even after refill, dispense still underflows.")
    
        # Refilled
        # now we know end_pos >= min_range
        self._machine.move(de0=travel_mm, s = s, wait=True)
        
        # Calculate Dispensed Volume
        Dispensed_Volume = abs(travel_mm) / self.mm_to_ml 

        # update the currentLiquidVolume for the well at sample_loc 
        self.update_currentLiquidVolume(volume= Dispensed_Volume, location= sample_loc, is_dispense= True) 

        # Return Dispensed Volume
        return Dispensed_Volume 

    @requires_active_tool
    def refill(self, refill_loc: Union[Well, Tuple, Location], s: int = 50):
        """Refill the syringe

        :param refill_loc: The location to refill the syringe from
        :type refill_loc: Union[Well, Tuple, Location]
        :param s: Speed at which to refill in mm/min, defaults to 2000
        :type s: int, optional
        """

        # Error handling to check if the labware at location has lid or not
        self.lid_on_top_error_handling(location= refill_loc, expected_condition = False) 

        # fully refill the syringe
        current_pos = float(self._machine.get_position()[self.e_drive])
        headroom_mm = self.max_range - current_pos

        # We need some prime_distance for priming of the syringe 
        prime_distance = -10
 
        # Calculate the actual mm to move
        actual_mm = headroom_mm + prime_distance 

        # Calculate the Aspirated Volume
        Aspirated_Volume = actual_mm / self.mm_to_ml

        # Error handling to check if the well at refill_loc has enough liquid or not 
        self.check_liquid_level(location=refill_loc, target_volume=Aspirated_Volume, is_dispense=False)
        

        # move to the refill location
        x, y, z = Labware._getxyz(refill_loc)

        self._machine.safe_z_movement()
        self._machine.move_to(x=x, y=y, wait= True)
        self._machine.move_to(z=z, wait= True)

        # aspirate from refill_loc
        self._machine.move(de0= headroom_mm, s = s, wait=True)

        # Priming of the left syringe after every refill for accurate first dose
        self._machine.move(de0 = prime_distance, s = s, wait = True) 

        # update the currentLiquidVolume for the well at refill_loc
        self.update_currentLiquidVolume(volume= Aspirated_Volume, location= refill_loc, is_dispense= False)

    @requires_active_tool
    def dispense(
        self, vol: float, sample_loc: Union[Well, Tuple, Location], refill_loc: Union[Well, Tuple, Location], s: int = 50
    ):
        """Dispense a certain volume into a given well.

        :param vol:  Volume to dispense, in milliliters
        :type vol: float
        :param location: The location to dispense the liquid into.
        :type location: Union[Well, Tuple, Location]
        :param s: Speed at which to dispense in mm/min, defaults to 2000
        :type s: int, optional

        """

        self._dispense(vol, sample_loc= sample_loc, refill_loc=refill_loc, s=s)
    @requires_active_tool
    def mix(
        self,
        loc: Union[Well, Tuple, Location],
        num_cycles: int = 1,
        vol: float = 0.0,
        s: int = 50,
    ):
        """Mixes liquid by alternating aspirate and dispense steps for the specified number of times

        :param num_cycles: The number of times to mix
        :type num_cycles: int
        :param vol: The volume of liquid to aspirate and dispense in uL
        :type vol: float
        :param s: The speed of the plunger movement in mm/min
        :type s: int
        """ 

        # Error handling to check if the labware at location has lid or not
        self.lid_on_top_error_handling(location= loc, expected_condition = False)
            
        x, y, z = Labware._getxyz(loc)
        self._machine.safe_z_movement()
        self._machine.move_to(x=x, y=y)
        self._machine.move_to(z=z)
        
        for i in range(0, num_cycles):
            self._aspirate(vol, location=loc, s=s)
            self._dispense(vol, sample_loc=loc, s=s)   # No need to provide refill_loc bcz there just has been aspiration of the same amount of liquid into the syringe

    @requires_active_tool 
    def reset_position(self, s: int = 400):
        """Resets the syringe position to the minimum position.
        """
        if self.e_drive is None:
            raise ToolStateError("No syringe is currently active")
        
        current_pos = float(self._machine.get_position()[self.e_drive])
        self._machine.move(de0=-current_pos, s=s, wait= True)

    @requires_active_tool
    def transfer(
        self,
        vol: float,
        s: int = 50,
        source: Well = None,
        destination: Well = None,
        mix_before: tuple = None,
        mix_after: tuple = None,
    ):
        """Transfer liquid from source well(s) to a set of destination well(s). Accommodates one-to-one, one-to-many, many-to-one, and uneven transfers.

        :param vol: Volume to transfer in milliliters
        :type vol: float
        :param s: Speed at which to aspirate and dispense in mm/min, defaults to 2000
        :type s: int, optional
        :param source: A source well or set of source wells, defaults to None
        :type source: Well, optional
        :param destination: A destination well or set of destination wells, defaults to None
        :type destination: Well, optional
        :param mix_before: Mix the source well before transfering, defaults to None
        :type mix_before: tuple, optional
        :param mix_after: Mix the destination well after transfering, defaults to None
        :type mix_after: tuple, optional
        """
        if type(source) != list:
            source = [source]
        if type(destination) != list:
            destination = [destination]

        # Assemble tuples of (source, destination)
        num_source_wells = len(source)
        num_destination_wells = len(destination)
        if num_source_wells == num_destination_wells:  # n to n transfers
            pass
        elif (
            num_source_wells == 1 and num_destination_wells > 1
        ):  # one to many transfers
            source = list(np.repeat(source, num_destination_wells))
        elif (
            num_source_wells > 1 and num_destination_wells == 1
        ):  # many to one transfers
            destination = list(np.repeat(destination, num_source_wells))
        elif num_source_wells > 1 and num_destination_wells > 1:  # uneven transfers
            # for uneven transfers, find least common multiple to pair off wells
            # raise a warning, as this might be a mistake
            # this mimics OT-2 behavior
            least_common_multiple = np.lcm(num_source_wells, num_destination_wells)
            source_repeat = least_common_multiple / num_source_wells
            destination_repeat = least_common_multiple / num_destination_wells
            source = list(np.repeat(source, source_repeat))
            destination = list(np.repeat(destination, destination_repeat))
            warnings.warn("Warning: Uneven source & destination wells specified.")

        source_destination_pairs = list(zip(source, destination))
        for source_well, destination_well in source_destination_pairs:
            # TODO: Large volume transfers which exceed tool capacity should be split up into several transfers
            xs, ys, zs = Labware._getxyz(source_well)
            xd, yd, zd = Labware._getxyz(destination_well)

            self._machine.safe_z_movement()
            self._machine.move_to(x=xs, y=ys)
            self._machine.move_to(z=zs + 5)
            self.current_well = source_well
            self._aspirate(vol, s=s)

            #             if mix_before:
            #                 self.mix(mix_before[0], mix_before[1])
            #             else:
            #                 pass

            self._machine.safe_z_movement()
            self._machine.move_to(x=xd, y=yd)
            self._machine.move_to(z=zd + 5)
            self.current_well = destination_well
            self._dispense(vol, s=s)


#             if mix_after:
#                 self.mix(mix_after[0], mix_after[1])
#             else:
#                 pass


