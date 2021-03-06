#!/usr/bin/python
import sys
import rospy
import smach

# import of generic states
import mir_states.common.manipulation_states as gms
import mcr_states.common.basic_states as gbs

# action lib
from smach_ros import ActionServerWrapper, IntrospectionServer

from std_msgs.msg import String
from mir_planning_msgs.msg import GenericExecuteAction, GenericExecuteResult, GenericExecuteFeedback
from mir_actions.utils import Utils
        
#===============================================================================

class SelectObject(smach.State):
    def __init__(self, topic_name):
        smach.State.__init__(self,  outcomes=['succeeded'],
                                    input_keys=['goal'],
                                    output_keys=['feedback', 'result'])
        self.publisher = rospy.Publisher(topic_name, String, queue_size=10)
        rospy.sleep(0.1) # time for the publisher to register in ros network

    def execute(self, userdata):
        # Add empty result msg (because if none of the state do it, action server gives error)
        userdata.result = GenericExecuteResult()
        userdata.feedback = GenericExecuteFeedback(current_state='SelectObject',
                                                   text='selecting object')

        obj = Utils.get_value_of(userdata.goal.parameters, 'object')
        self.publisher.publish(String(data=obj))
        rospy.sleep(0.2) # let the topic to survive for some time
        return 'succeeded'
        
#===============================================================================

def main():
    # Open the container
    rospy.init_node('pick_object_wbc_server')
    # Construct state machine
    sm = smach.StateMachine(
            outcomes=['OVERALL_SUCCESS','OVERALL_FAILED'],
            input_keys = ['goal'],
            output_keys = ['feedback', 'result'])

    with sm:
        smach.StateMachine.add('SELECT_OBJECT', SelectObject(
                    '/mcr_perception/object_selector/input/object_name'),
                transitions={'succeeded':'GENERATE_OBJECT_POSE'})
      
        # generates a pose of object
        smach.StateMachine.add('GENERATE_OBJECT_POSE', gbs.send_and_wait_events_combined(
                event_in_list=[('/mcr_perception/object_selector/event_in','e_trigger')],
                event_out_list=[('/mcr_perception/object_selector/event_out','e_selected', True)],
                timeout_duration=10),
                transitions={'success':'OPEN_GRIPPER',
                             'timeout':'OVERALL_FAILED',
                             'failure':'OVERALL_FAILED'})

        smach.StateMachine.add('OPEN_GRIPPER', gms.control_gripper('open'),
            transitions={'succeeded': 'SET_DBC_PARAMS'})

        smach.StateMachine.add('SET_DBC_PARAMS', gbs.set_named_config('dbc_pick_object'),
                transitions={'success':'MOVE_ROBOT_AND_PICK',
                             'timeout':'OVERALL_FAILED',
                             'failure':'OVERALL_FAILED'})

        # whole body control command. It moves direct base controller and
        # calls pre-grasp planner, and (optionally) moves arm to object pose
        smach.StateMachine.add('MOVE_ROBOT_AND_PICK', gbs.send_and_wait_events_combined(
                event_in_list=[('/wbc/event_in','e_start')],
                event_out_list=[('/wbc/event_out','e_success', True)],
                timeout_duration=50),
                transitions={'success':'CLOSE_GRIPPER',
                             'timeout':'STOP_MOVE_ROBOT_TO_OBJECT_WITH_FAILURE',
                             'failure':'STOP_MOVE_ROBOT_TO_OBJECT_WITH_FAILURE'})

        smach.StateMachine.add('STOP_MOVE_ROBOT_TO_OBJECT_WITH_FAILURE', gbs.send_event(
                    [('/waypoint_trajectory_generation/event_in','e_start'),
                     ('/wbc/event_in', 'e_stop')]),
                transitions={'success':'OVERALL_FAILED'})

        smach.StateMachine.add('CLOSE_GRIPPER', gms.control_gripper('close'),
                transitions={'succeeded': 'MOVE_ARM_TO_STAGE_INTERMEDIATE'})

        # move arm to stage_intemediate position
        smach.StateMachine.add('MOVE_ARM_TO_STAGE_INTERMEDIATE', gms.move_arm('stage_intermediate'), 
                transitions={'succeeded':'VERIFY_OBJECT_GRASPED', 
                             'failed':'MOVE_ARM_TO_STAGE_INTERMEDIATE'})

        smach.StateMachine.add('VERIFY_OBJECT_GRASPED', gbs.send_and_wait_events_combined(                 
                event_in_list=[('/gripper_controller/grasp_monitor/event_in','e_trigger')],                                  
                event_out_list=[('/gripper_controller/grasp_monitor/event_out','e_object_grasped', True)],                          
                timeout_duration=5),                                                                                   
                transitions={'success':'OVERALL_SUCCESS',                                                       
                             'timeout':'OVERALL_FAILED',                                                       
                             'failure':'OVERALL_FAILED'})
        
    # smach viewer
    if rospy.get_param('~viewer_enabled', False):
        sis = IntrospectionServer('pick_object_smach_viewer', sm, '/PICK_OBJECT_SMACH_VIEWER')
        sis.start()
    
    # Construct action server wrapper
    asw = ActionServerWrapper(
        server_name = 'wbc_pick_object_server',
        action_spec = GenericExecuteAction,
        wrapped_container = sm,
        succeeded_outcomes = ['OVERALL_SUCCESS'],
        aborted_outcomes   = ['OVERALL_FAILED'],
        preempted_outcomes = ['PREEMPTED'],
        goal_key     = 'goal',
        feedback_key = 'feedback',
        result_key   = 'result')
    # Run the server in a background thread
    asw.run_server()
    rospy.spin()
        
if __name__ == '__main__':
   main()
