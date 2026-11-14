import json
import os
import string 
from dataclasses import dataclass 
from typing import Dict, Iterable, List, NamedTuple, Tuple, Union 

import time  
from datetime import datetime, timedelta 
import matplotlib.pyplot as plt
import pandas as pd 
import numpy as np

from science_jubilee.Machine import Machine
from science_jubilee.tools.Tool import Tool
from science_jubilee.decks.Deck import Deck
from science_jubilee.labware.Labware import Labware, Location, Well
from science_jubilee.tools.Double_Syringe import DoubleSyringe
from science_jubilee.tools.Syringe import Syringe
from science_jubilee.tools.Vacuum_Gripper import VacuumGripper 
from science_jubilee.tools.Oceandirect_axo import Spectrometer  


# Caution:[Done] While coding, keep in mind that sample_labware_sy/ssy/spec [vial_name] refer to different well objects. Hence when you apply any ops like dispense only the currentLiquidVolume paramter for that Well object will be updated. 
# Hence use only sample_labware_sy for all the single/dual syringe operations and in the spectromter operations use sample_labware_spec but update the sample_labware_sy Well object side-by-side. 

# TODO: [Done] Add the Lid handling code wherever needed with all the conditions. Do it for the record_spectrum and make_vial functions where we consider lids over the vials also and cover them after the op if completed. 
# TODO: [Done] Repair the problem with local and global copies of the self.samples2_sy (global labware class)-> sample_labware_sy(we make changes in well objects of it) -> self.samples2_sy(but the global labware class remains the same as before)
# TODO: Make the code more general and ask the user to tell where the well_number n slot_number where the precursors and solvents are kept. 

class Experiment:
    """
    A class to represent an experiment.
    """ 

    def __init__(self, machine: Machine, deck: Deck, all_tools: Dict[str, Tool], all_labwares: Dict[str, Dict[str, Labware]]):
        """
        Initialize an Experiment object. 
        :param machine: Machine object
        :type machine: Machine
        :param deck: Deck object
        :type deck: Deck
        :param all_tools: Dictionary of all tools
        :type all
        :param all_labwares: Dictionary of all labwares
        :type all_labwares: Dict[str, Dict[str, Labware]]
        """

        self.machine = machine
        self.deck = deck
        self.all_tools = all_tools
        self.all_labwares = all_labwares 

        self.init_expand() 

    
    def init_expand(self):
        """
        Function to initialise all the tools and labwares
        """ 

        # Tools
        self.single_syringe = self.all_tools["single_syringe"]
        self.dual_syringe = self.all_tools["dual_syringe"]
        self.gripper = self.all_tools["gripper"]
        self.spectrometer = self.all_tools["spectrometer"]

        # Labwares 
        self.slot1 = self.all_labwares["slot1"]
        self.slot2 = self.all_labwares["slot2"]
        self.slot3 = self.all_labwares["slot3"]
        self.slot4 = self.all_labwares["slot4"]
        self.slot5 = self.all_labwares["slot5"]

        self.precursors = self.slot1["precursors"]
        self.samples2_sy = self.slot2["samples2_sy"]
        self.samples2_ssy = self.slot2["samples2_ssy"]
        self.samples2_spec = self.slot2["samples2_spec"]
        self.solvents = self.slot3["solvents"]
        self.vacuum_location = self.slot4["vacuum_location"]
        self.samples5_sy = self.slot5["samples5_sy"]
        self.samples5_ssy = self.slot5["samples5_ssy"]
        self.samples5_spec = self.slot5["samples5_spec"]

        # some more variables
        self.need_spectrum_refs = True # Flag to check if the spectrum references are already present

        # Timing of the readings (BO-6 log: putting both lids on ~53 s, taking them off ~55 s, probe pick-up to
        # "Spectrum recorded" ~35 s).
        # lid_min_gap_s: put the sample/solvent lids on between readings only if the next reading is at least this
        #   far away, so that the lids can go on and come off again without delaying it (was 90 s, which made
        #   readings 30-60 s late whenever the gap was 90-150 s).
        # lead_s_lids_on / lead_s_lids_off: start record_spectrum this long before a reading, with / without lids
        #   to take off first (was 94 s in both cases, which made readings without lids ~1 min early).
        # lids_between_readings: False keeps the lids off until the last reading of the round (e.g. 2.5-min level).
        self.lid_min_gap_s = 150
        self.lead_s_lids_on = 94
        self.lead_s_lids_off = 36
        self.lids_between_readings = True
        # keep_dual_syringe_filled: at the end of the dispensing step park the dual syringe with its precursor
        #   solutions instead of emptying it back into the reservoirs (the next round uses them). Default False.
        self.keep_dual_syringe_filled = False

        # save the log_file_path 
        log_dir = self.spectrometer.experiment_dir
        log_file = "operations_log.txt"
        os.makedirs(log_dir, exist_ok=True)

        # Full path to the log file
        self.log_path = os.path.join(log_dir, log_file)


    def make_batch(self, file_name: str) :
        """
        Function to create a batch of samples  
        :param file_name: Name of the Json file which contains the details of the samples to be synthesized
        :type file_name: str 

        Here the procedure of synthesis n spectrum recording is as follows:
        1. Dispense solvent in all the vials one-by-one
        2. Apply make_vial on all the vials one-by-one
            2.1 check precursor volumes in the dual syringe (refill if insufficient), then dispense metal precursor followed immediately by organic precursor
            2.2 mix the solution
            2.3 Take T0 spectrum reading 
            2.4 wash the probe 
        3. Record spectrum readings at regular intervals for all the vials

        """ 
        axo = self.machine
        precursors = self.precursors
        solvents = self.solvents


        with open(file_name, "r") as f:
            self.data = json.load(f)

        # Extract data out of the json file 
        self.spectrum_record_interval_mins = self.data["spectrum_record_interval_mins"] 
        self.max_spectrum_records = self.data["max_spectrum_records"] 

        # Parse all the slot names from the json 
        self.slot_names = []
        for k in self.data.keys():
            k = k.lower().strip()
            if k[:4]=="slot":
                 self.slot_names.append(k) 

        # Check the (optional) "addition_order" of every vial before any hardware operation starts
        for slot_name in self.slot_names:
            for vial_name, vial_param in self.data[slot_name].items():
                if vial_param.get("addition_order", "metal_first") not in ("metal_first", "organic_first"):
                    raise ValueError(f"{slot_name} {vial_name}: addition_order must be 'metal_first' or 'organic_first', got '{vial_param['addition_order']}'")

        # Start every batch from the same deck state as the first batch of a session: all lids stacked in slot 4
        self.stow_all_lids()

        # Save the vial recipes (optional)
        self.save_vial_recipes()  

        # Record the spectrometer reference spectrum if needed 
        if self.need_spectrum_refs:
            self.record_spectrum_refs() 
            self.need_spectrum_refs = False
        else: 
            pass 

        # Step 1. Dispense solvents in all the vials one-by-one
        self.dispense_solvent_all_vials() 

        # Loop over all the slots for step 2
        axo.pickup_tool(self.dual_syringe)
        self.log_operation('Picked Up Dual Syringe')
        
        for slot_name in self.slot_names: 
            slot = int(slot_name[4:]) if slot_name[4:].isdigit() else slot_name[4:]

            all_vials = self.data[slot_name]    # a dict with "A1": {}, "A2": {} ...

            # Loop over all the vials in the slot
            for vial_name, vial_param in all_vials.items(): 
                metal_precursor_vol = vial_param["metal_precursor_vol"]
                organic_precursor_vol = vial_param["organic_precursor_vol"]
                solvent_vol = vial_param["solvent_vol"]
                addition_order = vial_param.get("addition_order", "metal_first")   # "metal_first" (default) or "organic_first"
                
                # Step 2. Check/refill precursors -> dispense the metal precursor then the organic precursor -> mix the solution -> take T0 spectrum reading -> wash the probe
                self.make_vial(slot= slot, vial_name= vial_name, metal_precursor_vol= metal_precursor_vol, organic_precursor_vol= organic_precursor_vol, solvent_vol= solvent_vol, addition_order= addition_order) 
            
        # Reset the dual syringe (or, when another round follows, park it with its precursor solutions)
        if self.keep_dual_syringe_filled:
            self.park_dual_syringe_keep_contents()
        else:
            self.reset_dual_syringe()

        # Step 3. Record spectrum till the end of the experiment 
        self.record_spectrum_till_end()

    def save_vial_recipes(self):
        """
        Function is used to save all the vial recipes in a mof_recipes.json file
        """

        # Parse the chemical names from the json  
        self.metal_precursor_name = self.data["Experiment"]["metal_precursor_name"]
        self.organic_precursor_name = self.data["Experiment"]["organic_precursor_name"]
        self.solvent_name = self.data["Experiment"]["solvent_name"]

        # Save the vial recipes in a mof_recipes.json file by looping over all the vials one by one
        # Loop over all the slots 
        for slot_name in self.slot_names: 
            slot = int(slot_name[4:]) if slot_name[4:].isdigit() else slot_name[4:]

            all_vials = self.data[slot_name]    # a dict with "A1": {}, "A2": {} ...

            # Loop over all the vials in the slot
            for vial_name, vial_param in all_vials.items(): 
                # slot_and_vial_name = slot_name + "_" + vial_name
                metal_precursor_vol = vial_param["metal_precursor_vol"]
                organic_precursor_vol = vial_param["organic_precursor_vol"]
                solvent_vol = vial_param["solvent_vol"] 

                self.spectrometer.record_mof_recipe(well_id= vial_name , metal_precursor_name= self.metal_precursor_name, metal_precursor_vol_ml = metal_precursor_vol, organic_precursor_name= self.organic_precursor_name, organic_precursor_vol_ml = organic_precursor_vol, solvent_name= self.solvent_name, solvent_vol_ml= solvent_vol, additional_notes= slot_name)

    def all_vials_made(self):
        """
        This function is used to check if all the vials have been synthesised or not
        Returns Bool: True/False
        """
        # Loop over all the slots 
        for slot_name in self.slot_names: 
            slot = int(slot_name[4:]) if slot_name[4:].isdigit() else slot_name[4:]

            # Choose the sample labware based on the slot number
            if slot == 2: 
                sample_labware_ssy = self.samples2_ssy
                sample_labware_sy = self.samples2_sy
                sample_labware_spec = self.samples2_spec
            elif slot ==5:
                sample_labware_ssy = self.samples5_ssy 
                sample_labware_sy = self.samples5_sy
                sample_labware_spec = self.samples5_spec 

            all_vials = self.data[slot_name]    # a dict with "A1": {}, "A2": {} ...

            # Loop over all the vials in the slot
            for vial_name, vial_param in all_vials.items(): 
                vial_well_obj = sample_labware_spec[vial_name] 

                # If any of the vials did not have their T0 spectrum recorded, we return False. 
                if vial_well_obj.num_readings_taken == 0:
                    return False
                
            return True
        
    def record_spectrum_refs(self):
        """
        Function to record spectrum references
        """
        axo = self.machine
        spectrometer = self.spectrometer
        solvents = self.solvents 

        # Lid on top error handling 
        if solvents.has_lid_on_top == True: 
            self.gripper_pick_and_place(slot_from= 3, slot_to= 4) 

        axo.pickup_tool(spectrometer)
        self.log_operation("Picked Up Spectrometer")

        spectrometer.position_probe(solvents[1].top(-23)) 

        spectrometer.configure_device()
        self.log_operation("Configured Spectrometer")

        self.log_operation(f"Positioned Probe to collect the dark reference spectrum. \nKindly switch off the probe light")
        choice = input("Press any key to continue: ")
        self.log_operation("Recording dark reference spectrum")
        spectrometer.set_dark()

        self.log_operation("Kindly switch on the probe light")
        choice = input("Press any key to continue: ")
        self.log_operation("Recording white reference spectrum")
        spectrometer.set_white() 


        axo.park_tool() 
        self.log_operation("Parked Spectrometer")

    def fill_dual_syringe(self):
        """
        Function to fill the dual syringe with dye
        """
        axo = self.machine 
        dual_syringe = self.dual_syringe 
        precursors = self.precursors  

        # Lid on top error handling 
        if precursors.has_lid_on_top == True: 
            self.gripper_pick_and_place(slot_from= 1, slot_to= 4)
        
        # Pick up the dual syringe 
        axo.pickup_tool(dual_syringe)
        self.log_operation("Picked Up Dual Syringe")

        # Refill both the syringe one by one
        dual_syringe.refill(drive = dual_syringe.e0_drive, refill_loc = precursors[0].top(-54), s = 100)
        dual_syringe.refill(drive = dual_syringe.e1_drive, refill_loc = precursors[0].top(-54), s = 100)
        self.log_operation("Dual Syringe Refilled") 

        # Park the dual syringe
        axo.park_tool()
        self.log_operation("Parked Dual Syringe") 
    
    def reset_dual_syringe(self):
        """
        Function to reset the dual syringe
        """
        axo = self.machine 
        dual_syringe = self.dual_syringe 
        precursors = self.precursors 

        # calculate the headroom_ml just for logging purposes
        drive0 = dual_syringe.e0_drive
        current_pos0 = float(dual_syringe._machine.get_position()[drive0])
        headroom_mm0 = current_pos0 - dual_syringe.min_range
        headroom_ml0 = headroom_mm0 / dual_syringe.mm_to_ml

        drive1 = dual_syringe.e1_drive
        current_pos1 = float(dual_syringe._machine.get_position()[drive1])
        headroom_mm1 = current_pos1 - dual_syringe.min_range
        headroom_ml1 = headroom_mm1 / dual_syringe.mm_to_ml

        # place the tool
        x, y, z = dual_syringe._xy_for_drive(drive1, precursors[0]) 
        dual_syringe._machine.safe_z_movement()
        dual_syringe._machine.move_to(x=x, y=y, wait=True)
        dual_syringe._machine.move_to(z=z, wait=True)

        # call the .reset_position func
        dual_syringe.reset_position()

        # update the currentLiquidVolume of the precursors
        dual_syringe.update_currentLiquidVolume(volume= headroom_ml0, location= precursors[1], is_dispense= True)
        dual_syringe.update_currentLiquidVolume(volume= headroom_ml1, location= precursors[0], is_dispense= True)

        # Log the resetting of the Dual Syringe
        current_pos0 = float(dual_syringe._machine.get_position()[drive0])
        current_pos1 = float(dual_syringe._machine.get_position()[drive1])
        self.log_operation(f"Dual Syringe Drive 0 reset and position: {current_pos0}")
        self.log_operation(f"Dual Syringe Drive 1 reset and position: {current_pos1}") 

        # Park the dual syringe
        axo.park_tool()
        self.log_operation("Parked Dual Syringe")  

        # Place lid over the precursors, now that all the vials have been synthesised 
        if precursors.has_lid_on_top == False:
            self.gripper_pick_and_place(slot_from= 4, slot_to= 1)  


    def park_dual_syringe_keep_contents(self):
        """
        End of the dispensing step when another round follows: park the dual syringe without emptying it (the
        precursor solutions stay in the syringes for the next round) and put the lid back on the precursors.
        """
        axo = self.machine
        dual_syringe = self.dual_syringe
        precursors = self.precursors

        ml0 = (float(dual_syringe._machine.get_position()[dual_syringe.e0_drive]) - dual_syringe.min_range) / dual_syringe.mm_to_ml
        ml1 = (float(dual_syringe._machine.get_position()[dual_syringe.e1_drive]) - dual_syringe.min_range) / dual_syringe.mm_to_ml
        self.log_operation(f"Dual Syringe kept filled for the next round: {ml0:.2f} ml metal precursor (drive 0), {ml1:.2f} ml organic precursor (drive 1)")

        # Park the dual syringe
        axo.park_tool()
        self.log_operation("Parked Dual Syringe")

        # Place lid over the precursors, now that all the vials have been synthesised
        if precursors.has_lid_on_top == False:
            self.gripper_pick_and_place(slot_from= 4, slot_to= 1)

    def return_dual_syringe_contents(self):
        """
        Empty the dual syringe back into the precursor reservoirs (end of a campaign, or before restarting the
        kernel / machine while it still holds precursor solutions).
        """
        if self.precursors.has_lid_on_top == True:
            self.gripper_pick_and_place(slot_from= 1, slot_to= 4)
        self.machine.pickup_tool(self.dual_syringe)
        self.log_operation("Picked Up Dual Syringe")
        self.reset_dual_syringe()

    def stow_all_lids(self):
        """
        Move every lid that is on the deck back onto the lid stack in slot 4, precursor lid first (bottom), then the
        solvent lid, then the sample-rack lid (top). This is the stack order the first batch of a session expects:
        lids are later taken from the top of the stack as rack -> solvent -> precursors.
        Needed when make_batch is called several times in one session (autonomous loop), because a finished batch
        leaves lids on slots 1, 3 and on the rack it used.
        """
        for slot, labware in ((1, self.precursors), (3, self.solvents), (2, self.samples2_spec), (5, self.samples5_spec)):
            if labware.has_lid_on_top == True:
                self.gripper_pick_and_place(slot_from= slot, slot_to= 4)

    def gripper_pick_and_place(self, slot_from: int, slot_to: int):
        """
        Function to pick up and place the gripper from one slot to another
        :param slot_from: Slot from which the gripper is to be picked up
        :type slot_from: int
        :param slot_to: Slot to which the gripper is to be placed
        :type slot_to: int
        """

        axo = self.machine
        gripper = self.gripper
        vacuum_location = self.vacuum_location

        # If another tool (e.g. a syringe) is in hand, park it first and pick it up again afterwards.
        # Otherwise the caller continues with a tool that is no longer the active tool (ToolStateError).
        held_index = axo.active_tool_index
        held_tool = axo.tools[held_index]["tool"] if held_index not in (None, -1) and held_index in axo.tools else None
        if held_tool is gripper:
            held_tool = None
        if held_tool is not None:
            axo.park_tool()
            self.log_operation(f"Parked {held_tool.name} to move a lid")

        axo.pickup_tool(gripper)
        self.log_operation("Picked Up Vacuum Gripper")

        gripper.pick_and_place(vacuum_location[slot_from],
                            vacuum_location[slot_to], 0.7, 3)
        self.log_operation(f"Picked Lid from Slot {slot_from} and Placed Lid in Slot {slot_to}")

        axo.park_tool()
        self.log_operation("Parked Vacuum Gripper")  

        if held_tool is not None:
            axo.pickup_tool(held_tool)
            self.log_operation(f"Picked Up {held_tool.name} again")

    
    def dispense_solvent_all_vials(self):
        """
        Function to dispense solvent to all vials
        """
        axo = self.machine
        single_syringe = self.single_syringe 
        solvents = self.solvents
        precursors = self.precursors 

        # Lid on top error handling 
        if solvents.has_lid_on_top == True: 
            self.gripper_pick_and_place(slot_from= 3, slot_to= 4) 


        # Pickup the tool 
        axo.pickup_tool(single_syringe)
        self.log_operation("Picked Up Single Syringe") 

        # Loop over all the slots
        for slot_name in self.slot_names: 
            slot = int(slot_name[4:]) if slot_name[4:].isdigit() else slot_name[4:]
            
            # Choose the sample labware based on the slot number
            if slot == 2: 
                sample_labware_ssy = self.samples2_ssy
                sample_labware_sy = self.samples2_sy
                sample_labware_spec = self.samples2_spec
            elif slot ==5:
                sample_labware_ssy = self.samples5_ssy 
                sample_labware_sy = self.samples5_sy
                sample_labware_spec = self.samples5_spec 

            # Lid on top error handling for the sample_labware_spec
            if sample_labware_spec.has_lid_on_top == True: 
                self.gripper_pick_and_place(slot_from= slot, slot_to= 4) 

            all_vials = self.data[slot_name]    # a dict with "A1": {}, "A2": {} ...

            # Loop over all the vials in the slot
            for vial_name, vial_param in all_vials.items(): 
                metal_precursor_vol = vial_param["metal_precursor_vol"]
                organic_precursor_vol = vial_param["organic_precursor_vol"]
                solvent_vol = vial_param["solvent_vol"] 


                # Fill the vial with solvent
                single_syringe.dispense(vol= solvent_vol, sample_loc= sample_labware_ssy[vial_name].top(0), refill_loc= solvents[0].top(-40), s= min(solvent_vol*200, 400))
                self.log_operation(f"Vial {vial_name} on Slot {slot} filled with {solvent_vol} ml solvent")
                self.log_operation("Sleep for 3 seconds to let the drops fall off")
                time.sleep(3)

                # Update the currentLiquidVolume for other Well objects 
                single_syringe.update_currentLiquidVolume(volume= solvent_vol, location= sample_labware_sy[vial_name], is_dispense=True )
                single_syringe.update_currentLiquidVolume(volume= solvent_vol, location= sample_labware_spec[vial_name], is_dispense=True )

        # Reset the single syringe position before parking because we have to use it later for mix ops 
        drive = single_syringe.e_drive 

        # Position the tool 
        x, y, z = Labware._getxyz(solvents[0])
        single_syringe._machine.safe_z_movement()
        single_syringe._machine.move_to(x=x, y=y)
        single_syringe._machine.move_to(z=z+20)

        # calculate the headroom_ml just for logging purposes
        current_pos = float(single_syringe._machine.get_position()[drive])
        headroom_mm = current_pos - single_syringe.min_range
        headroom_ml = headroom_mm / single_syringe.mm_to_ml

        # reset the single syringe
        single_syringe.reset_position()

        current_pos = float(single_syringe._machine.get_position()[drive])        
        self.log_operation(f"Single Syringe reset and position: {current_pos}")

        # update the currentLiquidVolume of the solvents[1] well
        single_syringe.update_currentLiquidVolume(volume= headroom_ml, location= solvents[0], is_dispense= True)

        # Park the single syringe
        axo.park_tool()
        self.log_operation("Parked Single Syringe")   

    def dispense_metal_precursor_all_vials(self):
        """
        Function to dispense metal precursor to all vials
        """ 

        axo = self.machine
        dual_syringe = self.dual_syringe
        precursors = self.precursors
        solvents = self.solvents
        precursors = self.precursors  

        # Lid on top error handling over the precursors
        if precursors.has_lid_on_top == True: 
            self.gripper_pick_and_place(slot_from= 1, slot_to= 4) 


        # Pickup the dual_syringe
        axo.pickup_tool(dual_syringe)
        self.log_operation("Picked Up Dual Syringe")

        # Loop over all the slots
        for slot_name in self.slot_names: 
            slot = int(slot_name[4:]) if slot_name[4:].isdigit() else slot_name[4:]
            
            # Choose the sample labware based on the slot number
            if slot == 2: 
                sample_labware_ssy = self.samples2_ssy
                sample_labware_sy = self.samples2_sy
                sample_labware_spec = self.samples2_spec
            elif slot ==5:
                sample_labware_ssy = self.samples5_ssy 
                sample_labware_sy = self.samples5_sy
                sample_labware_spec = self.samples5_spec 

            # Lid on top error handling for the sample_labware_spec
            if sample_labware_spec.has_lid_on_top == True: 
                self.gripper_pick_and_place(slot_from= slot, slot_to= 4) 

            all_vials = self.data[slot_name]    # a dict with "A1": {}, "A2": {} ...

            # Loop over all the vials in the slot
            for vial_name, vial_param in all_vials.items(): 
                metal_precursor_vol = vial_param["metal_precursor_vol"]
                organic_precursor_vol = vial_param["organic_precursor_vol"]
                solvent_vol = vial_param["solvent_vol"]  

                # Dispense the metal precursor into the vial
                dual_syringe.dispense_e0(vol= metal_precursor_vol, sample_loc_e=sample_labware_sy[vial_name].top(-13), refill_loc_e=precursors[0].top(-50), s=min(125*metal_precursor_vol, 400))
                self.log_operation(f"Sleeping for 3 seconds to let the drops fall off")
                time.sleep(3)
                self.log_operation(f"Vial {vial_name} on Slot {slot} filled with {metal_precursor_vol} ml metal precursor ") 

                # update the currentLiquidVolume for other Well objects
                dual_syringe.update_currentLiquidVolume(volume= metal_precursor_vol, location= sample_labware_ssy[vial_name], is_dispense=True )
                dual_syringe.update_currentLiquidVolume(volume= metal_precursor_vol, location= sample_labware_spec[vial_name], is_dispense=True ) 

    def make_vial(self, slot: int, vial_name: str, metal_precursor_vol: float, organic_precursor_vol: float, solvent_vol: float, addition_order: str = "metal_first"):
        """
        Function to make a vial
        :param slot: Slot in which the vial is to be placed
        :type slot: int
        :param vial_name: Name of the vial
        :type vial_name: str
        :param metal_precursor_vol: Volume of metal precursor to be added to the vial
        :type metal_precursor_vol: float
        :param organic_precursor_vol: Volume of organic precursor to be added to the vial
        :type organic_precursor_vol: float
        :param solvent_vol: Volume of solvent to be added to the vial
        :type solvent_vol: float  
        :param addition_order: "metal_first" (metal precursor, then organic precursor) or "organic_first" (organic precursor, then metal precursor)
        :type addition_order: str

        This function is used to do the following routine:
        1. Check precursor volumes in the dual syringe (refill if insufficient), then dispense the metal precursor followed by the organic precursor
        2. Mix the vial
        3. Take T0 reading 
        4. Wash the probe 
        """

        axo = self.machine
        single_syringe = self.single_syringe
        dual_syringe = self.dual_syringe
        solvents = self.solvents 
        precursors = self.precursors

        # Choose the sample labware based on the slot number
        if slot == 2: 
            sample_labware_ssy = self.samples2_ssy
            sample_labware_sy = self.samples2_sy
            sample_labware_spec = self.samples2_spec
        elif slot ==5:
            sample_labware_ssy = self.samples5_ssy 
            sample_labware_sy = self.samples5_sy
            sample_labware_spec = self.samples5_spec 

        # Lid on top error handling for the sample_labware_spec
        if sample_labware_spec.has_lid_on_top == True: 
            self.gripper_pick_and_place(slot_from= slot, slot_to= 4) 

        # Lid on top error handling for the precursors
        if precursors.has_lid_on_top == True:
            self.gripper_pick_and_place(slot_from= 1, slot_to= 4) 

        # Check if the dual syringe has sufficient metal precursor (drive 0) and organic precursor (drive 1) for this vial; if not, refill before dispensing
        headroom_ml0 = (float(dual_syringe._machine.get_position()[dual_syringe.e0_drive]) - dual_syringe.min_range) / dual_syringe.mm_to_ml
        headroom_ml1 = (float(dual_syringe._machine.get_position()[dual_syringe.e1_drive]) - dual_syringe.min_range) / dual_syringe.mm_to_ml
        if headroom_ml0 < metal_precursor_vol:
            dual_syringe.refill(drive = dual_syringe.e0_drive, refill_loc = precursors[0].top(-50), s = 200)
            self.log_operation("Dual Syringe Drive 0 (metal precursor) Refilled")
        if headroom_ml1 < organic_precursor_vol:
            dual_syringe.refill(drive = dual_syringe.e1_drive, refill_loc = precursors[0].top(-50), s = 200)
            self.log_operation("Dual Syringe Drive 1 (organic precursor) Refilled")

        # Dispense the metal precursor and the organic precursor back-to-back, in the order given by addition_order
        if addition_order == "organic_first":
            precursor_order = ["organic", "metal"]
        else:
            precursor_order = ["metal", "organic"]
        self.log_operation(f"Vial {vial_name} on Slot {slot}: addition order = {precursor_order[0]} precursor -> {precursor_order[1]} precursor")

        for precursor in precursor_order:
            if precursor == "metal":
                # Dispense the metal precursor into the vial
                dual_syringe.dispense_e0(vol= metal_precursor_vol, sample_loc_e=sample_labware_sy[vial_name].top(-13), refill_loc_e=precursors[0].top(-50), s=min(125*metal_precursor_vol, 400))
                self.log_operation(f"Sleeping for 3 seconds to let the drops fall off")
                time.sleep(3)
                self.log_operation(f"Vial {vial_name} on Slot {slot} filled with {metal_precursor_vol} ml metal precursor ")

                # update the currentLiquidVolume for other Well objects
                dual_syringe.update_currentLiquidVolume(volume= metal_precursor_vol, location= sample_labware_ssy[vial_name], is_dispense=True )
                dual_syringe.update_currentLiquidVolume(volume= metal_precursor_vol, location= sample_labware_spec[vial_name], is_dispense=True )

            else:
                # Dispense the organic precursor into the vial
                dual_syringe.dispense_e1(vol= organic_precursor_vol, sample_loc_v=sample_labware_sy[vial_name].top(-13), refill_loc_v=precursors[0].top(-50), s=organic_precursor_vol*150)
                self.log_operation(f"Sleeping for 3 seconds to let the drops fall off")
                time.sleep(3)
                self.log_operation(f"Vial {vial_name} on Slot {slot} filled with {organic_precursor_vol} ml organic precursor ")

                # update the currentLiquidVolume for other Well objects
                dual_syringe.update_currentLiquidVolume(volume= organic_precursor_vol, location= sample_labware_ssy[vial_name], is_dispense=True )
                dual_syringe.update_currentLiquidVolume(volume= organic_precursor_vol, location= sample_labware_spec[vial_name], is_dispense=True )

        self.log_operation(f"Vial {vial_name} on Slot {slot} filled with {metal_precursor_vol} ml metal precursor and {organic_precursor_vol} ml organic precursor")

        # Park the dual syringe 
        axo.park_tool()
        self.log_operation("Parked Dual Syringe")  

        # Pickup the single syringe
        axo.pickup_tool(single_syringe)
        self.log_operation("Picked Up Single Syringe")

        # Mix the vial Solution 
        single_syringe.mix(vol = 3, loc = sample_labware_ssy[vial_name].top(-48), num_cycles = 1, s = 400)
        self.log_operation(f"Vial {vial_name} on Slot {slot} mixed") 

        # Park the single syringe
        axo.park_tool()
        self.log_operation("Parked Single Syringe")

        # Take the T0 spectrum reading 
        self.record_spectrum(slot, vial_name)

        # Pickup the dual syringe
        axo.pickup_tool(dual_syringe)
        self.log_operation("Picked Up Dual Syringe")


    def record_spectrum(self, slot, vial_name):
        """
        Function to record spectrum for a given vial
        Parameters:
            slot (int): Slot number
            vial_name (str): Vial name
        """
        axo = self.machine
        spectrometer = self.spectrometer
        solvents = self.solvents
        gripper = self.gripper
        vacuum_location = self.vacuum_location

        # Choose the sample labware based on the slot number 
        if slot == 2:
            sample_labware_sy = self.samples2_sy
            sample_labware_ssy = self.samples2_ssy
            sample_labware_spec = self.samples2_spec
        elif slot== 5:
            sample_labware_sy = self.samples5_sy
            sample_labware_ssy = self.samples5_ssy
            sample_labware_spec = self.samples5_spec

        # Lid on top error handling for the solvents
        if (solvents.has_lid_on_top == True) and (sample_labware_spec.has_lid_on_top == True):
            axo.pickup_tool(gripper)
            self.log_operation("Picked Up Vacuum Gripper")
            gripper.pick_and_place(vacuum_location[3],vacuum_location[4],0.7, 3)
            self.log_operation("Remove Lid From Solvent")
            gripper.pick_and_place(vacuum_location[slot],vacuum_location[4],0.7, 3)
            self.log_operation("Remove Lid From Samples")
            axo.park_tool()
            self.log_operation("Parked Vacuum Gripper")

        # Pickup the spectrometer 
        axo.pickup_tool(spectrometer)
        self.log_operation("Picked Up Spectrometer") 

        # change the num_readings_taken attribute for all the Well objects 
        sample_labware_sy[vial_name].num_readings_taken += 1
        sample_labware_ssy[vial_name].num_readings_taken += 1
        sample_labware_spec[vial_name].num_readings_taken += 1
        
        # Record the spectrum for the given vial 
        elapsed_min = (sample_labware_spec[vial_name].num_readings_taken -1)* self.spectrum_record_interval_mins
        # Label times as "2.5" or "5" (never "5.0") so that CSV columns and log lines stay parseable
        elapsed_min = round(float(elapsed_min), 3)
        if elapsed_min.is_integer():
            elapsed_min = int(elapsed_min)

        wavelengths, vals, absorbance = spectrometer.collect_spectrum(sample_labware_spec[vial_name].top(-40), elapsed_min= elapsed_min, save= True)
        spectrometer.plot_spectrum(sample_labware_spec[vial_name].top(-40), elapsed_min= elapsed_min , show_plot=False, save_plot=True)  
        wavelengths, vals, absorbance = spectrometer.collect_spectrum(sample_labware_spec[vial_name].top(-40), elapsed_min= elapsed_min, save= True)
        spectrometer.plot_spectrum(sample_labware_spec[vial_name].top(-40), elapsed_min= elapsed_min , show_plot=False, save_plot=True)  
        wavelengths, vals, absorbance = spectrometer.collect_spectrum(sample_labware_spec[vial_name].top(-40), elapsed_min= elapsed_min, save= True)
        spectrometer.plot_spectrum(sample_labware_spec[vial_name].top(-40), elapsed_min= elapsed_min , show_plot=False, save_plot=True)  
        self.log_operation(f"Spectrum recorded for vial {vial_name} on Slot {slot} at {elapsed_min} mins") 

        # update the next_spectrum_recordtime for all the Well objects 
        sample_labware_sy[vial_name].next_spectrum_recordtime = datetime.now() + timedelta(minutes=self.spectrum_record_interval_mins)
        sample_labware_ssy[vial_name].next_spectrum_recordtime = datetime.now() + timedelta(minutes=self.spectrum_record_interval_mins)
        sample_labware_spec[vial_name].next_spectrum_recordtime = datetime.now() + timedelta(minutes=self.spectrum_record_interval_mins)

        # Wash the probe 
        spectrometer.wash_probe(solvents[1].top(-23), n_cycles= 3)
        self.log_operation("Washed Spectrometer Probe 3 Times") 

        # Park the spectrometer 
        axo.park_tool() 
        self.log_operation("Parked Spectrometer ") 


        # Check if the nearest spectrum record time is within lid_min_gap_s seconds from now. 
        # Get the nearest next_spectrum_recordtime
        next_slot, next_vial = self.nearest_spectrum_reading()
        # choose the sample labware based on the slot number
        if next_slot == 2: 
            sample_labware_ssy = self.samples2_ssy
            sample_labware_sy = self.samples2_sy 
            sample_labware_spec = self.samples2_spec
        elif next_slot == 5:
            sample_labware_ssy = self.samples5_ssy
            sample_labware_sy = self.samples5_sy
            sample_labware_spec = self.samples5_spec
        if next_vial is None:
            # No more scheduled measurements (all readings taken): treat the next measurement as infinitely far away so that the lids are replaced
            nearest_timediff = timedelta.max
        else:
            # Get the Well object of the next_vial
            vial_well_obj = sample_labware_spec[next_vial]
            # Calculate the time difference between the current time and the next_spectrum_recordtime
            nearest_timediff = vial_well_obj.next_spectrum_recordtime - datetime.now()

        # revert the lid on top of the sample_labware_spec only if all the vials have been synthesised
        # (with lids_between_readings == False the lids go on only after the last reading of the round)
        lid_now = (next_vial is None) or (self.lids_between_readings and nearest_timediff > timedelta(seconds=self.lid_min_gap_s))
        if sample_labware_spec.has_lid_on_top == False and self.all_vials_made() == True and lid_now:
            axo.pickup_tool(gripper)
            self.log_operation("Picked Up Vacuum Gripper")
            gripper.pick_and_place(vacuum_location[4],vacuum_location[slot],0.7, 3)
            self.log_operation("Put Lid to Samples")
            gripper.pick_and_place(vacuum_location[4],vacuum_location[3],0.7, 3)
            self.log_operation("Put Lid to Solvent")
            axo.park_tool()
            self.log_operation("Parked Vacuum Gripper")

    def nearest_spectrum_reading(self):
        """
        Returns the Slot and Vial whose next_spectrum_recordtime is nearest from the current time 
        returns:
            next_slot: int   (None if no vial needs any further reading)
            next_vial: str   (None if no vial needs any further reading)
        """ 

        """
        Strategy :  1. If the next_spectrum_recordtime for some vial is in the past, we give the highest priority to that vial. If the the next_spectrum_recordtime is too past in time from the current time, we may also consider not taking that reading
                    2. Otherwise, we give the highest priority to the vial whose next_spectrum_recordtime is in future the nearest from the current time
        """
        axo = self.machine

        # Loop through all the vials and see which one has the nearest next_spectrum_recordtime attribute 
        smallest_timediff = timedelta(minutes= 10000)
        next_slot, next_vial = None, None   # stays None if no vial needs any further reading
        
        # Loop through all the slots
        for slot_name in self.slot_names:
            
            # Choose the sample labware based on the slot number
            if slot_name == "slot2":
                sample_labware_ssy = self.samples2_ssy
                sample_labware_sy = self.samples2_sy 
                sample_labware_spec = self.samples2_spec
            elif slot_name == "slot5":
                sample_labware_ssy = self.samples5_ssy
                sample_labware_sy = self.samples5_sy
                sample_labware_spec = self.samples5_spec
            
            slot = int(slot_name[4:]) if slot_name[4:].isdigit() else slot_name[4:]

            all_vials = self.data[slot_name]    # a dict with "A1": {}, "A2": {} ...

            # Loop through all the vials in the slot
            for vial_name, vial_param in all_vials.items():

                vial_well_obj = sample_labware_sy[vial_name]

                # Ensure that the vial's next_spectrum_recordtime is not None and also ensure that we "need" to take the next readings for the vial
                if vial_well_obj.next_spectrum_recordtime is not None and vial_well_obj.num_readings_taken < self.max_spectrum_records:

                    time_diff = vial_well_obj.next_spectrum_recordtime - datetime.now()       
                    # If somehow a vial's next_spectrum_recordtime is in the past, then its timediff will be the most negative. And accordingly it will be given the utmost priority. 

                    if time_diff < smallest_timediff:
                        smallest_timediff = time_diff
                        next_slot = slot
                        next_vial = vial_name 

        # Return only after ALL slots have been checked
        return next_slot, next_vial

    def record_spectrum_if_needed(self):
        """
        Function to record spectrum if needed 
        return bool : not flag_var 
        """ 

        flag_var = True          # This flag checks if we need to take another reading or else we are fine with only one reading
        is_any_reading_taken = False  # This flag checks if any reading has been taken till now.

        while flag_var: 
            # Break this loop if we dont need to take any more readings and now we could restart our synthesis of other vials left. 

            # Get the nearest next_spectrum_recordtime
            next_slot, next_vial = self.nearest_spectrum_reading()

            # choose the sample labware based on the slot number
            if next_slot == 2: 
                sample_labware_ssy = self.samples2_ssy
                sample_labware_sy = self.samples2_sy 
                sample_labware_spec = self.samples2_spec
            elif next_slot == 5:
                sample_labware_ssy = self.samples5_ssy
                sample_labware_sy = self.samples5_sy
                sample_labware_spec = self.samples5_spec

            # Get the Well object of the next_vial
            vial_well_obj = sample_labware_spec[next_vial]

            # Calculate the time difference between the current time and the next_spectrum_recordtime
            timediff = vial_well_obj.next_spectrum_recordtime - datetime.now()

            # Check if we can make another vial or if we urgently need to record the nearest spectrum recording 
            if timediff < timedelta(minutes = self.make_vial_time_mins): 

                while True:   # Keep running the loop until the current datetime reaches the next_spectrum_recordtime of the vial
                    time_now = datetime.now()

                    if time_now >= vial_well_obj.next_spectrum_recordtime- timedelta(seconds=20):
                        self.record_spectrum(next_slot, next_vial) 
                        is_any_reading_taken = True
                        break
                    else:
                        time.sleep(3)  # Sleep for 3 seconds before checking again

            else:
                flag_var = False 

        return is_any_reading_taken

    
    def record_spectrum_till_end(self):
        """
        Function to take spectrum readings for the vials till the end of the experiement
        """
        axo = self.machine
        spectrometer = self.spectrometer 

        
        while True:
            # Break the loop when all the vials have num_readings_taken >= self.max_spectrum_records 
 
            # check if all recordings have been taken or not 
            flag_break = self.all_recordings_taken()

            # If all the vials have num_readings_taken >= self.max_spectrum_records, break the loop
            if flag_break == False:
                # Get the nearest next_spectrum_recordtime
                next_slot, next_vial = self.nearest_spectrum_reading() 


                # Choose the sample labware based on the slot number
                if next_slot == 2:
                    sample_labware_ssy = self.samples2_ssy
                    sample_labware_sy = self.samples2_sy 
                    sample_labware_spec = self.samples2_spec
                elif next_slot == 5:
                    sample_labware_ssy = self.samples5_ssy
                    sample_labware_sy = self.samples5_sy
                    sample_labware_spec = self.samples5_spec


                # Get the Well object for the next_vial 
                vial_well_obj = sample_labware_spec[next_vial]

                # Calculate the time difference between the current time and the next_spectrum_recordtime
                timediff = vial_well_obj.next_spectrum_recordtime - datetime.now()

                # Start early enough to reach the vial on time; removing the lids first takes ~1 min extra
                lids_on = (self.solvents.has_lid_on_top == True) and (sample_labware_spec.has_lid_on_top == True)
                lead = timedelta(seconds= self.lead_s_lids_on if lids_on else self.lead_s_lids_off)

                while True:   # Keep running the loop until the current datetime reaches the next_spectrum_recordtime of the vial
                    if datetime.now() >= vial_well_obj.next_spectrum_recordtime- lead:
                        self.record_spectrum(next_slot, next_vial)
                        break
                    else:
                        time.sleep(3)   # Wait for 3 seconds before checking again 

            else:
                # Break out of the loop when all the readings have been taken. And end the operations
                break 

        self.log_operation("Experiment Completed Successfully!!")


    def all_recordings_taken(self):
        """
        Functions return True is all the recordings have been taken ; else False 
        """

        flag_var= True

        # Loop over all the slots
        for slot_name in self.slot_names: 
            slot = int(slot_name[4:]) if slot_name[4:].isdigit() else slot_name[4:]

            # Choose the sample labware based on the slot number
            if slot == 2:
                sample_labware_ssy = self.samples2_ssy
                sample_labware_sy = self.samples2_sy 
                sample_labware_spec = self.samples2_spec
            elif slot == 5:
                sample_labware_ssy = self.samples5_ssy
                sample_labware_sy = self.samples5_sy
                sample_labware_spec = self.samples5_spec 

            all_vials = self.data[slot_name]    # a dict with "A1": {}, "A2": {} ...

            # Check if all the vials have num_readings_taken >= self.max_spectrum_records. Otherwise put flag_var = False
            for vial_name, vial_param in all_vials.items(): 
                if sample_labware_spec[vial_name].num_readings_taken < self.max_spectrum_records: 
                    flag_var = False  
                    break


        return flag_var

                
    def log_operation(self, message: str):
        """
        Function to log the operation into the operation log .txt file
        """

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{now}] {message}\n" 

        print(entry)
        
        with open(self.log_path, "a") as file:  # 'a' for append mode
            file.write(entry) 

