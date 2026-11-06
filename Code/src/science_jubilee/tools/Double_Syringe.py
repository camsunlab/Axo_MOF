import json
import os
from typing import Tuple, Union

from science_jubilee.labware.Labware import Labware, Location, Well
from science_jubilee.tools.Tool import (
    Tool, 
    ToolConfigurationError, 
    ToolStateError, 
    requires_active_tool,
) 

class DoubleSyringe(Tool):
    def __init__(
        self,
        index: int,
        name: str,
        config: str,
        offset_x : float = 48
    ):
        super().__init__(index, name)

        # 1) load the single hardware config
        self.min_range = 0
        self.max_range = None
        self.mm_to_ml = None

        # will be set in post_load()
        self.e0_drive = None
        self.e1_drive = None
        self.offset_x : float = offset_x
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
        """Figure out which axis letter is E vs. V."""
        tools = json.loads(self._machine.gcode('M409 K"tools[]"'))["result"]
        for tool in tools:
            if tool["number"] == self.index:
                drives = tool["extruders"]         # e.g. [0,1]
                self.e0_drive = f"E{drives[0]}"
                if len(drives) > 1:
                    self.e1_drive = f"E{drives[1]}"
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
    def _aspirate(self, vol: float, drive : str, s: int = 50):
        """Aspirate a certain volume in milliliters. Used only to move the syringe; to aspirate from a particular well, see aspirate()

        :param vol: Volume to aspirate, in milliliters
        :type vol: float
        :param s: Speed at which to aspirate in mm/min, defaults to 2000
        :type s: int, optional
        """
        #de = vol * self.mm_to_ml
        pos = self._machine.get_position()
        
        # Current E position in mm
        current_pos = float(self._machine.get_position()[drive])
        print(f"Current_pos = {current_pos}")
        # Current range of travel for vol
        headroom_mm = self.max_range - current_pos
        #print(f"Headroom_mm = {headroom_mm}")
        # Desired travel (in mm) for vol
        desired_mm = vol * self.mm_to_ml
        #print(f"Desired_mm = {desired_mm}")
        # Cap into the available space
        actual_mm = min(desired_mm, headroom_mm)
        #print(f"Actual_mm = {actual_mm}")
        if actual_mm <= 0:
            # already full 
            
            return "Syringe already full; no aspirate performed"
        
        end_pos = current_pos + actual_mm
        
        self.check_bounds(end_pos)
            
        if drive == self.e0_drive:
            #print(f"Performing e0 move")
            self._machine.move(de0 = actual_mm, s = s, wait=True)
        elif drive == self.e1_drive:
            #print(f"Performing e1 move")
            self._machine.move(de1 = actual_mm, s = s, wait=True)
            
        #self._machine.move(de = actual_mm, wait = True)
        #self.check_bounds(end_pos)
        #self._machine.move(de=de, wait=True)
        
        # Return how many volume actually pulled in
        actual_ml = actual_mm / self.mm_to_ml
        return actual_ml
    
    
    def _refill(self, drive : str, loc : Union[Well, Tuple, Location], s: int = 50):

        # Current E position in mm
        current_pos = float(self._machine.get_position()[drive])
        headroom_mm = self.max_range - current_pos
        
        # We need to dispense some ml for priming purposes
        prime_distance = -10 

        # Calculate the actual mm to move
        actual_mm = headroom_mm + prime_distance 

        # Calculate the aspirate volume
        Aspirate_Volume = actual_mm / self.mm_to_ml

        # Error handling to check if the well at refill_loc has enough liquid or not 
        self.check_liquid_level(location = loc, target_volume= Aspirate_Volume, is_dispense = False)

        if drive == self.e0_drive:
            #print(f"Performing e0 move")
            self._machine.move(de0 = headroom_mm, s = s, wait=True)

            # Priming of the left syringe after every refill for accurate first dose
            self._machine.move(de0 = prime_distance, s = s, wait = True)
        elif drive == self.e1_drive:
            #print(f"Performing e1 move")
            self._machine.move(de1 = headroom_mm, s = s, wait=True)

            # Priming of the right syringe after every refill for accurate first dose
            self._machine.move(de1 = prime_distance, s = s, wait = True)

        actual_mm = headroom_mm + prime_distance

        # Aspirated_Volume 
        Aspirated_Volume = actual_mm / self.mm_to_ml 

        # Since while doing all the dispense_e0() and dispense_e1() calls, the refill_loc is given out to be precursors[0] well obj, instead of precursors[1] well obj. Hence we are reverting it here.
        if drive == self.e0_drive: 
            # pick out the well obj of the loc parameter 
            if isinstance(loc, Well):
                well_obj = loc 
            elif isinstance(loc, Location):
                well_obj = loc._labware
            
            # Get the precursor[1] well obj from the precursors[0] well obj and update the location of the well obj 
            labware_obj = well_obj.labware_obj
            loc_update = labware_obj['A2'] # precursors[1] well obj

            loc = loc_update

        # update the currentLiquidVolume for the well at loc 
        self.update_currentLiquidVolume(volume= Aspirated_Volume, location = loc, is_dispense = False)

    @requires_active_tool
    def refill(
        self, drive: str, refill_loc: Union[Well, Tuple, Location], s : int = 50):
        """
            Always refill the desired_volume that user wants
        """

        # Error handling to check if the labware at refill_loc has lid or not
        self.lid_on_top_error_handling(location= refill_loc, expected_condition = False) 

        refill_x, refill_y, refill_z = Labware._getxyz(refill_loc)
    
        self._machine.safe_z_movement()
        self._machine.move_to(x = refill_x, y = refill_y)
        self._machine.move_to(z = refill_z)
        self._refill(drive = drive, loc = refill_loc, s = s)

        # if drive == self.e0_drive:
        #     # You have to decide which precursor for each MOTOR drive (Precursors)
        #     refill_x, refill_y, refill_z = Labware._getxyz(refill_loc)
        
        #     self._machine.safe_z_movement()
        #     self._machine.move_to(x = refill_x, y = refill_y)
        #     self._machine.move_to(z = refill_z)
        #     self._refill(drive = drive, loc = refill_loc, s = s)
        
        # elif drive == self.e1_drive:
        #     # You have to decide which precursor for each MOTOR drive (Precursors)
        #     refill_x, refill_y, refill_z = Labware._getxyz(refill_loc)
        
        #     self._machine.safe_z_movement()
        #     self._machine.move_to(x = refill_x, y = refill_y)
        #     self._machine.move_to(z = refill_z)
        #     self._refill(drive = drive, loc= refill_loc, s = s)
        
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
        x, y, z = Labware._getxyz(location)

        # self._machine.safe_z_movement()
        self._machine.move_to(x=x, y=y)
        self._machine.move_to(z=z)
        aspirated_ml = self._aspirate(vol, s=s)

        return aspirated_ml
    
    #@requires_active_tool
    def dispense(
        self, vol: float, location: Union[Well, Tuple, Location], s: int = 50
    ):
        """Dispense a certain volume into a given well.

        :param vol:  Volume to dispense, in milliliters
        :type vol: float
        :param location: The location to dispense the liquid into.
        :type location: Union[Well, Tuple, Location]
        :param s: Speed at which to dispense in mm/min, defaults to 2000
        :type s: int, optional

        """
        x, y, z = Labware._getxyz(location)

        # self._machine.safe_z_movement()
        self._machine.move_to(x=x, y=y)
        self._machine.move_to(z=z)
        dispensed_ml = self._dispense(vol, s=s)
        
        return dispensed_ml

###############################################################################################################################
    
    def _plunger(self, drive : str, delta_mm: float, s : int):
        """Move E0/E1 by delta_mm in RELATIVE extrusion mode."""
        self._machine._set_relative_extrusion()             #  M83
        if drive == self.e0_drive:
            self._machine.move(de0=delta_mm, s=s, wait=True)
        else:
            self._machine.move(de1=delta_mm, s=s, wait=True)
        self._machine._set_absolute_extrusion()             #  M82
    
    # Helper function for right syringe (0) offset from left syringe (1)
    def _xy_for_drive(self,
                      drive : str,
                      loc: Union[Well, Tuple, Location]):
        """Return (x, y, z) applying + offset_x for E0"""
        x, y, z = Labware._getxyz(loc)
        if drive == self.e0_drive:
            x += self.offset_x
        return x, y, z
    
    @requires_active_tool
    def _move_channel(
        self,
        drive: str,
        loc: Union[Well, Tuple, Location],
        vol: float,
        s: int,
        dispense: bool,
        refill_loc: Union[Well, Tuple, Location] = None,
    ) -> float:
        
        """Core routine for aspirate (dispense=False) or dispense (dispense=True),
        with out‑of‑bounds & refill logic baked in."""
        #  1) resolve coordinates
        #if isinstance(loc, str):
        #    coords = (self.locations_e if drive == self.e_drive else self.locations_v)[loc]
        #else:
        #    coords = loc

        # 2) move XYZ 
        #self._machine.safe_z_movement()
        #self._machine.move_to(x=x, y=y)
        #self._machine.move_to(z=z)


        # Error handling to check if the labware at loc has lid or not
        self.lid_on_top_error_handling(location= loc, expected_condition = False) 

        # Error handling to check if the well at loc has enough liquid or not(For aspirate only) 
        # and to check if the target dispense volume does not overfill the well (For dispense only)
        self.check_liquid_level(location=loc, target_volume=vol, is_dispense= dispense)
            
            
        # 3) compute the mm travel for this vol 
        travel_mm = vol * self.mm_to_ml * (-1 if dispense else 1)

        # 4) check bounds and possibly refill (for dispense)
        current_pos = float(self._machine.get_position()[drive])

        # This is to account for the rounding errors which makes travel_mm slightly more negative than the current_pos sometimes
        if (travel_mm > -current_pos-0.2) and (travel_mm < -current_pos):
            travel_mm = -current_pos

        end_pos = current_pos + travel_mm

        if not dispense:
            # aspirate: if we can't move any further, syringe is full
            headroom_mm = self.max_range - current_pos
            actual_mm = min(travel_mm, headroom_mm)
            if actual_mm <= 0:
                # already at max_range
                return "Syringe already full; no aspirate performed"
            end_pos = current_pos + actual_mm
            self.check_bounds(end_pos)

            # 2) move XYZ
            x, y, z = self._xy_for_drive(drive, loc) 
            self._machine.safe_z_movement()
            self._machine.move_to(x=x, y=y, wait=True)
            self._machine.move_to(z=z, wait = True)

            if drive == self.e0_drive:
                self._machine.move(de0=actual_mm, s = s, wait=True)
            else:
                self._machine.move(de1=actual_mm, s = s, wait=True)
            
            Aspirated_Volume = actual_mm / self.mm_to_ml 

            # update the currentLiquidVolume for the well at loc 
            self.update_currentLiquidVolume(volume= Aspirated_Volume, location = loc, is_dispense = False)

            # Return Aspirated Volume     
            return Aspirated_Volume
        # ----------------Dispensing -------------------------#
        else:
            # dispense: if end_pos < 0, Need to refill first
            if end_pos < self.min_range:
                #print(f"Needs to Refill the Syringe {drive}")
                if refill_loc is None:
                    raise ToolStateError(f"{drive}: dispense below 0 without refill location.")
                # how much volume to aspirate to full?
                headroom_ml = (self.max_range - current_pos) / self.mm_to_ml
                # aspirate from refill_loc
                #self._move_channel(drive, refill_loc, headroom_ml, s = 500, dispense=False)
                self.refill(drive = drive, refill_loc=refill_loc, s = 150)
                # return to the original well coords
                
            x, y, z = self._xy_for_drive(drive, loc) 
            self._machine.safe_z_movement()
            self._machine.move_to(x=x, y=y, wait=True)
            self._machine.move_to(z=z, wait=True)
                
            # recompute pos now at full, end_pos = full + de
            refilled_pos = float(self._machine.get_position()[drive])
            end_pos = refilled_pos + travel_mm
            if end_pos < self.min_range:
                raise ToolStateError(f"{drive}: even after refill, dispense still underflows.")
        
            # Refilled
            # now we know end_pos >= min_range
            if drive == self.e0_drive:
                self._machine.move(de0=travel_mm, s = s, wait=True)
            else:
                self._machine.move(de1=travel_mm, s = s, wait=True)
            
            Dispensed_Volume = abs(travel_mm) / self.mm_to_ml 


            # update the currentLiquidVolume of the well at loc 
            self.update_currentLiquidVolume(volume= Dispensed_Volume, location = loc, is_dispense = True)

            # Return Dispensed Volume
            return Dispensed_Volume

    ##################################################################
    # Drive‑Specific (Literal["E", "V"]) convenience methods:

    @requires_active_tool
    def aspirate_e0(self,
                   vol: float,
                   refill_loc_e: Union[Well, Tuple, Location], 
                   s: int = 50) -> float:
        return self._move_channel(self.e0_drive, refill_loc_e, vol, s, dispense=False)

    @requires_active_tool
    def dispense_e0(
        self,
        vol: float,
        sample_loc_e: Union[Well, Tuple, Location],
        refill_loc_e: Union[Well, Tuple, Location],
        s: int = 50,
    ) -> float:
        return self._move_channel(self.e0_drive, 
                                  sample_loc_e, 
                                  vol, s, 
                                  dispense=True, 
                                  refill_loc=refill_loc_e)

    @requires_active_tool
    def aspirate_e1(self, 
                   vol: float, 
                   refill_loc_v: str, 
                   s: int = 50) -> float:
        return self._move_channel(self.e1_drive, refill_loc_v, vol, s, dispense=False)

    @requires_active_tool
    def dispense_e1(
        self,
        vol: float,
        sample_loc_v: Union[Well, Tuple, Location],
        refill_loc_v: Union[Well, Tuple, Location],
        s: int = 50,
    ) -> float:
        return self._move_channel(self.e1_drive, 
                                  sample_loc_v, 
                                  vol, 
                                  s , 
                                  dispense=True, 
                                  refill_loc=refill_loc_v)
    
    # After experimentation empty the syringe
    @requires_active_tool 
    def reset_position(self, s: int = 350):
        """
        Moves both syringes (E0 and E1) to position 0 (fully dispensed).

        :param s: Speed at which to move in mm/min, defaults to 2000
        :type s: int, optional
        """
        if self.e0_drive is None or self.e1_drive is None:
            raise ToolConfigurationError("Drives not assigned. Make sure post_load() has been run.")

        current_pos_0 = float(self._machine.get_position()[self.e0_drive])
        current_pos_1 = float(self._machine.get_position()[self.e1_drive])
        
        self._machine.move(de0=-current_pos_0, de1=-current_pos_1, s=s, wait=True) 


