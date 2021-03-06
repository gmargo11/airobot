import threading
import time

import airobot.utils.common as arutil
from airobot.ee_tool.ee import EndEffectorTool
from airobot.utils.arm_util import wait_to_reach_jnt_goal


class Robotiq2F140Pybullet(EndEffectorTool):
    """
    Class for interfacing with a Robotiq 2F140 gripper when
    it is attached to UR5e arm in pybullet.

    Args:
        cfgs (YACS CfgNode): configurations for the gripper.
        pb_client (BulletClient): pybullet client.

    Attributes:
        cfgs (YACS CfgNode): configurations for the gripper.
        gripper_close_angle (float): position value corresponding to the
            fully closed position of the gripper.
        gripper_open_angle (float): position value corresponding to the
            fully open position of the gripper.
        jnt_names (list): names of the gripper joints.
        gripper_jnt_ids (list): pybullet joint ids of the gripper joints.
        robot_id (int): robot id in Pybullet.
        jnt_to_id (dict): mapping from the joint name to joint id.
    """

    def __init__(self, cfgs, pb_client):
        self._pb = pb_client
        super(Robotiq2F140Pybullet, self).__init__(cfgs=cfgs)
        self._gripper_mimic_coeff = [1, -1, 1, -1, -1, 1]
        self.jnt_names = [
            'finger_joint', 'left_inner_knuckle_joint',
            'left_inner_finger_joint', 'right_outer_knuckle_joint',
            'right_inner_knuckle_joint', 'right_inner_finger_joint',
        ]

        self._max_torque = 5.0
        self.gripper_close_angle = self.cfgs.EETOOL.CLOSE_ANGLE
        self.gripper_open_angle = self.cfgs.EETOOL.OPEN_ANGLE
        self._mthread_started = False
        self.deactivate()

    def feed_robot_info(self, robot_id, jnt_to_id):
        """
        Setup the gripper, pass the robot info from the arm to the gripper.

        Args:
            robot_id (int): robot id in Pybullet.
            jnt_to_id (dict): mapping from the joint name to joint id.

        """

        self.robot_id = robot_id
        self.jnt_to_id = jnt_to_id
        self.gripper_jnt_ids = [
            self.jnt_to_id[jnt] for jnt in self.jnt_names
        ]
        self._pb.changeDynamics(self.robot_id,
                                self.jnt_to_id['left_inner_finger_pad_joint'],
                                lateralFriction=2.0,
                                spinningFriction=1.0,
                                rollingFriction=1.0)
        self._pb.changeDynamics(self.robot_id,
                                self.jnt_to_id['right_inner_finger_pad_joint'],
                                lateralFriction=2.0,
                                spinningFriction=1.0,
                                rollingFriction=1.0)
        # if the gripper has been activated once,
        # the following code is used to prevent starting
        # a new thread after the arm reset if a thread has been started

        if not self._mthread_started:
            self._mthread_started = True
            # gripper thread
            self._th_gripper = threading.Thread(target=self._th_mimic_gripper)
            self._th_gripper.daemon = True
            self._th_gripper.start()
        else:
            return

    def open(self, wait=True):
        """
        Open the gripper.

        Returns:
            bool: return if the action is sucessful or not.
        """
        if not self._is_activated:
            raise RuntimeError('Call activate function first!')
        success = self.set_pos(self.gripper_open_angle,
                               wait=wait)
        return success

    def close(self, wait=True):
        """
        Close the gripper.

        Returns:
            bool: return if the action is sucessful or not.
        """
        if not self._is_activated:
            raise RuntimeError('Call activate function first!')
        success = self.set_pos(self.gripper_close_angle,
                               wait=wait)
        return success

    def set_pos(self, pos, wait=True):
        """
        Set the gripper position.

        Args:
            pos (float): joint position.
            wait (bool): wait until the joint position is set
                to the target position.

        Returns:
            bool: A boolean variable representing if the action is
            successful at the moment when the function exits.
        """
        joint_name = self.jnt_names[0]
        tgt_pos = arutil.clamp(pos,
                               self.gripper_open_angle,
                               self.gripper_close_angle)
        jnt_id = self.jnt_to_id[joint_name]
        self._pb.setJointMotorControl2(self.robot_id,
                                       jnt_id,
                                       self._pb.POSITION_CONTROL,
                                       targetPosition=tgt_pos,
                                       force=self._max_torque)
        if not self._pb.in_realtime_mode():
            self._set_rest_joints(tgt_pos)

        success = False
        if self._pb.in_realtime_mode() and wait:
            success = wait_to_reach_jnt_goal(
                tgt_pos,
                get_func=self.get_pos,
                joint_name=joint_name,
                get_func_derv=self.get_vel,
                timeout=self.cfgs.ARM.TIMEOUT_LIMIT,
                max_error=self.cfgs.ARM.MAX_JOINT_ERROR
            )
        return success

    def get_pos(self):
        """
        Return the joint position(s) of the gripper.

        Returns:
            float: joint position.
        """
        if not self._is_activated:
            raise RuntimeError('Call activate function first!')
        jnt_id = self.jnt_to_id[self.jnt_names[0]]
        pos = self._pb.getJointState(self.robot_id, jnt_id)[0]
        return pos

    def get_vel(self):
        """
        Return the joint velocity of the gripper.

        Returns:
            float: joint velocity.
        """
        if not self._is_activated:
            raise RuntimeError('Call activate function first!')
        jnt_id = self.jnt_to_id[self.jnt_names[0]]
        vel = self._pb.getJointState(self.robot_id, jnt_id)[1]
        return vel

    def disable_gripper_self_collision(self):
        """
        Disable the gripper collision checking in Pybullet.
        """
        if not self._is_activated:
            raise RuntimeError('Call activate function first!')
        for i in range(len(self.jnt_names)):
            for j in range(i + 1, len(self.jnt_names)):
                jnt_idx1 = self.jnt_to_id[self.jnt_names[i]]
                jnt_idx2 = self.jnt_to_id[self.jnt_names[j]]
                self._pb.setCollisionFilterPair(self.robot_id,
                                                self.robot_id,
                                                jnt_idx1,
                                                jnt_idx2,
                                                enableCollision=0)

    def _mimic_gripper(self, joint_val):
        """
        Given the value for the first joint,
        mimic the joint values for the rest joints.
        """
        jnt_vals = [joint_val]
        for i in range(1, len(self.jnt_names)):
            jnt_vals.append(joint_val * self._gripper_mimic_coeff[i])
        return jnt_vals

    def _th_mimic_gripper(self):
        """
        Make all the other joints of the gripper
        follow the motion of the first joint of the gripper.
        """
        while True:
            if self._is_activated and self._pb.in_realtime_mode():
                self._set_rest_joints()
            time.sleep(0.005)

    def _set_rest_joints(self, gripper_pos=None):
        max_torq = self._max_torque
        max_torques = [max_torq] * (len(self.jnt_names) - 1)
        if gripper_pos is None:
            gripper_pos = self.get_pos()
        gripper_poss = self._mimic_gripper(gripper_pos)[1:]
        gripper_vels = [0.0] * len(max_torques)
        self._pb.setJointMotorControlArray(self.robot_id,
                                           self.gripper_jnt_ids[1:],
                                           self._pb.POSITION_CONTROL,
                                           targetPositions=gripper_poss,
                                           targetVelocities=gripper_vels,
                                           forces=max_torques)

    def deactivate(self):
        """
        Deactivate the gripper.
        """
        self._is_activated = False

    def activate(self):
        """
        Activate the gripper.
        """
        self._is_activated = True
