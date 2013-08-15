#!/usr/bin/env python
"""
=================================================
CSharpRobotInit.py - CSharpRobot Initialization Handler
=================================================
"""

import time, math
from socket import *
from struct import pack,unpack
from threading import Thread, Lock, Event
import threading, subprocess, os
from numpy import matrix
import ltlmopMsg_pb2
import sys
from google.protobuf.message import DecodeError
import logging


class initHandler:
    def __init__(self,proj,robotType,IPAddress = '10.0.0.86',commPort=7400,buffer=1048576):
        """
        Open sockets for communication.
        robotType (int): robot type to be used PIONEER = 1, SEGWAY = 2 (default=1)
        IPAddress (string): ip of the robot (default=10.0.0.86)
        commPort (int): port on the robot for communcation between CSharp and LTLMoP (default=7400)
        buffer (int): size of the port buffer for receiving TCP messages from C# (default=1048576)
        """
        try:
            # Create proxies to access modules
            self.robocomm = _CSharpCommunicator(proj,robotType,IPAddress,commPort,buffer)
            self.robocomm.start()
            
            time.sleep(1)   # Give communicator time to start and receive first data
        except RuntimeError:
            self.robocomm.stop()
            print "(INIT) ERROR: Cannot connect to the robot."
            exit(-1)
            
    def getSharedData(self):
        # Return dictionary of module proxies for other handlers to use
        return {'robocomm':self.robocomm}
 
class _CSharpCommunicator:

    # Constructor
    def __init__(self,proj,robotType,IPAddress,commPort,mybuffer):
        """
        Open sockets for communication.
        """
        
        # Communication parameters
        self.IPAddress = IPAddress
        self.commPort = commPort
        self.buffer = mybuffer
    
        self.proj = proj
        # Communication parameters
        self.addFrom = (self.IPAddress,self.commPort)
        self.TCPSock = socket(AF_INET,SOCK_STREAM)
        self.TCPSock.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)
        self.TCPSock.settimeout(1)
        self.RobotType = robotType
        self.responseStr = ""

        # Sensor Status
        self.ARTAG = []
        self.LIDAR = []
        self.BUSY_EXPLORE = False
        self.pose = None
        
    def start(self):
        """
        Open sockets for communication.
         """
        import regions
        # establishes connection between LTLMoP and C#
        print 'Establishing connection with the C# side...'
        self.TCPSock.connect(self.addFrom)
        print 'Done.'
        
        ltlmop_msg = ltlmopMsg_pb2.PythonRequestMsg()
        ltlmop_msg.id=1
        ltlmop_msg.robot = self.RobotType
        self.responseStr = self.sendMessage(ltlmop_msg)
        time.sleep(1)
        # if we get a parsable acknowledgement, we are good to go!
        print "C# Acknowledgement: ",self.responseStr.id
        self.responseStr = ""

    def stop(self):
        """
        Close sockets and prevent further communication.
        """
        self.TCPSock.close()

    def sendDirection(self,direction):
        """
        Serialize and send the direction vector command to the robot.
        Also used to send velocity commands to the robot.

        direction (tuple containing 2 doubles):(x,y) or (v,w) depending on application.
        """
        # get pose handler
        self.pose_handler = self.proj.h_instance['pose']
        pose = self.pose_handler.getPose()
        ltlmop_msg = ltlmopMsg_pb2.PythonRequestMsg()
        ltlmop_msg.id = 2
        ltlmop_msg.vel.v     = direction[0]     # This can be either lin_velocity or a waypoint's x coordinate.
        ltlmop_msg.vel.omega = direction[1]     # This can be either ang_velocity or a waypoint's y coordinate.
        ltlmop_msg.pose.x = pose[0]
        ltlmop_msg.pose.y = pose[1]
        ltlmop_msg.pose.yaw = pose[2]
        response = self.sendMessage(ltlmop_msg)
        self.updateSensorStatus(response)
        
    def _receivePacket(self):
        msg = self._receiveBytes(4) # 32-bit length field = 4 bytes
        length = unpack("!1L", msg)[0] 
        
        return self._receiveBytes(length)
        
    def _receiveBytes(self, msglen):
        msg = ''
        while len(msg) < msglen:
            chunk = self.TCPSock.recv(msglen-len(msg))
            if chunk == '':
                raise RuntimeError("socket connection broken")
            msg = msg + chunk
        return msg    
        
    def sendMessage(self,message):
        """
        Serializes the message and send it to CSharp Interface via TCP
        and store the response message into class object self.response

        message (ltlmopMsg_pb2.PythonRequestMsg object): protobuff object to be sent
        """
        success = False
        message.ResendRequest = False
        result = ""
        numOfAttempts = 0
        logging.debug("Sending message with ID={}...".format(message.id))
        # TODO: threadlock?
        
        temp_serialized = message.SerializeToString()
        sent_str = pack('!I',len(temp_serialized))+temp_serialized
        self.TCPSock.send(sent_str)
                
        while ((not success) and (numOfAttempts<20)):
            numOfAttempts = numOfAttempts +1
            try:
                tic = time.time()
                response = self._receivePacket()
                toc = time.time()
                logging.debug("Received response packet in {}ms".format(1000*(toc-tic)))

                result = self.parseResponse(response)
                
                logging.debug("size: {} bytes, id: {}".format(len(response), result.id))
                
                success = True
            except Exception as e:
                logging.warning(str(e))
                #message.ResendRequest = True
                logging.warning("Failed to receive message.")
        return result
            
    
    def parseResponse(self,encryptedMsg):
        """
        Helper method that parses the serialized string from CSharp and returns
        a Protobuff message string

        encryptedMsg: serialized message to be parsed
        """
        csharp_response = ltlmopMsg_pb2.PythonRequestMsg()
        
        if len(encryptedMsg)>0:
            return csharp_response.FromString(encryptedMsg)
        else:
            return csharp_response
            
    def updateSensorStatus(self,msg):
        self.LIDAR = []
        self.ARTAG = []
        self.BUSY_EXPLORE = False
        logging.debug(msg)     
        for s in msg.sensors:
            if (s.type==ltlmopMsg_pb2.PythonRequestMsg.LIDAR):
                # we have lidar update
                self.LIDAR = s.data
            if (s.type==ltlmopMsg_pb2.PythonRequestMsg.ARTAG):
                self.ARTAG = s.data # this is all the ARTag IDs we got
        
        if msg.id == 13 and msg.pose is not None:
            self.pose = msg.pose
            logging.debug('got some pose! {} {} {}'.format(self.pose.x, self.pose.y, self.pose.yaw))
            
        self.BUSY_EXPLORE = msg.actuator.status==ltlmopMsg_pb2.PythonRequestMsg.RESP_BUSY;
        
               
    def getARTAG(self):
        result = self.ARTAG;
        return self.ARTAG

    def getLIDAR(self):
        result = self.LIDAR
        return self.LIDAR
    def getExploreBusy(self):
        result = self.BUSY_EXPLORE
        return self.BUSY_EXPLORE
    def getPose(self):
        #print 'getting pose for pose handler!'
        return self.pose
        
    
        