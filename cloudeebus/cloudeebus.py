#!/usr/bin/env python

# Cloudeebus
#
# Copyright 2012 Intel Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Luc Yriarte <luc.yriarte@intel.com>
# Christophe Guiraud <christophe.guiraud@intel.com>
#


import argparse, dbus, json, sys

from twisted.internet import glib2reactor
# Configure the twisted mainloop to be run inside the glib mainloop.
# This must be done before importing the other twisted modules
glib2reactor.install()
from twisted.internet import reactor, defer

from autobahn.websocket import listenWS
from autobahn.wamp import exportRpc, WampServerFactory, WampCraServerProtocol

from dbus.mainloop.glib import DBusGMainLoop

import gobject
import re
import dbus.service
gobject.threads_init()

from dbus import glib
glib.init_threads()

# enable debug log
from twisted.python import log

# XML parser module
from xml.etree.ElementTree import XMLParser

# For debug only
import os

###############################################################################

VERSION = "0.2.1"
OPENDOOR = False
CREDENTIALS = {}
WHITELIST = []

###############################################################################
class DbusCache:
    '''
    Global cache of DBus connexions and signal handlers
    '''
    def __init__(self):
        self.dbusConnexions = {}
        self.signalHandlers = {}


    def reset(self):
        '''
        Disconnect signal handlers before resetting cache.
        '''
        self.dbusConnexions = {}
        # disconnect signal handlers
        for key in self.signalHandlers:
            self.signalHandlers[key].disconnect()
        self.signalHandlers = {}


    def dbusConnexion(self, busName):
        if not self.dbusConnexions.has_key(busName):
            if busName == "session":
                self.dbusConnexions[busName] = dbus.SessionBus()
            elif busName == "system":
                self.dbusConnexions[busName] = dbus.SystemBus()
            else:
                raise Exception("Error: invalid bus: %s" % busName)
        return self.dbusConnexions[busName]



###############################################################################
class DbusSignalHandler:
    '''
    signal hash id as busName#senderName#objectName#interfaceName#signalName
    '''
    def __init__(self, busName, senderName, objectName, interfaceName, signalName):
        self.id = "#".join([busName, senderName, objectName, interfaceName, signalName])
        # connect handler to signal
        self.bus = cache.dbusConnexion(busName)
        self.bus.add_signal_receiver(self.handleSignal, signalName, interfaceName, senderName, objectName)
        
    
    def disconnect(self):
        names = self.id.split("#")
        self.bus.remove_signal_receiver(self.handleSignal, names[4], names[3], names[1], names[2])


    def handleSignal(self, *args):
        '''
        publish dbus args under topic hash id
        '''
        factory.dispatch(self.id, json.dumps(args))



###############################################################################
class DbusCallHandler:
    '''
    deferred reply to return dbus results
    '''
    def __init__(self, method, args):
        self.pending = False
        self.request = defer.Deferred()
        self.method = method
        self.args = args


    def callMethod(self):
        '''
        dbus method async call
        '''
        self.pending = True
        self.method(*self.args, reply_handler=self.dbusSuccess, error_handler=self.dbusError)
        return self.request


    def dbusSuccess(self, *result):
        '''
        return JSON string result array
        '''
        self.request.callback(json.dumps(result))
        self.pending = False


    def dbusError(self, error):
        '''
        return dbus error message
        '''
        self.request.errback(error.get_dbus_message())
        self.pending = False



################################################################################       
class exec_code:
    def __init__(self) :
        self.exec_string = ""
        self.exec_code = None
        self.exec_code_valid = 1
        self.indent_level = 0
        self.indent_increment = 1
        self.line = 0

    # __str__ : Return a string representation of the object, for
    # nice printing.
    def __str__(self) :
        return self.exec_string

    def p(self) :
        print str(self)

    def append_stmt(self, stmt) :
        self.exec_code_valid = 0
        self.line += 1
        if (stmt != "\n"):
            for x in range(0,self.indent_level):
                self.exec_string = self.exec_string + ' '            
            self.exec_string = self.exec_string + stmt + "\t\t# l:" + str(self.line) + '\n'
        else:
            if (stmt == "\n"):
                self.exec_string = self.exec_string + "# l:" + str(self.line) + '\n'
            else:
                self.exec_string = self.exec_string + stmt + "\t\t# l:" + str(self.line) + '\n'

    def indent(self) :
        self.indent_level = self.indent_level + self.indent_increment

    def dedent(self) :
        self.indent_level = self.indent_level - self.indent_increment
    
    # compile : Compile exec_string into exec_code using the builtin
    # compile function. Skip if already in sync.
    def compile(self) :
        if not self.exec_code_valid :
            self.exec_code = compile(self.exec_string, "<string>", "exec")
        self.exec_code_valid = 1

    def execute(self) :
        if not self.exec_code_valid :
            self.compile()
        exec self.exec_code



################################################################################       
class XmlCb_Parser: # The target object of the parser
    maxDepth = 0
    depth = 0
    def __init__(self, dynDBusClass):
        self.dynDBusClass = dynDBusClass
        
    def start(self, tag, attrib):   # Called for each opening tag.
        if (tag == 'node'):
            return
        # Set interface name
        if (tag == 'interface'):
            self.dynDBusClass.set_interface(attrib['name'])
            return
        # Set method name
        if (tag == 'method'):
            self.current = tag
            self.dynDBusClass.def_method(attrib['name'])
            return
        if (tag == 'signal'):
            self.current = tag
            self.dynDBusClass.def_signal(attrib['name'])
            return

        # Set signature (in/out & name) for method
        if (tag == 'arg'):
            if (self.current == 'method'):
                self.dynDBusClass.add_signature(attrib['name'],
                                                attrib['direction'],
                                                attrib['type'])
                return
            if (self.current == 'signal'):
                self.dynDBusClass.add_signature(attrib['name'], 'in',
                                                attrib['type'])
                return
    def end(self, tag):             # Called for each closing tag.
        if (tag == 'method'):
            self.dynDBusClass.add_dbus_method()
            self.dynDBusClass.add_body_method()
            self.dynDBusClass.end_method()
        if (tag == 'signal'):
            self.dynDBusClass.add_dbus_signal()
            self.dynDBusClass.add_body_signal()
            self.dynDBusClass.end_method()
           
    def data(self, data):
        pass            # We do not need to do anything with data.
    def close(self):    # Called when all data has been parsed.
        return self.maxDepth


       
################################################################################       
class dynDBusClass():
    def __init__(self, className, globalCtx, localCtx):
        self.className = className
        self.xmlCB = XmlCb_Parser(self)
        self.localCtx = localCtx
        self.globalCtx = globalCtx        
        self.signature = {}
        self.class_code = exec_code()  
        self.class_code.indent_increment = 4
        self.class_code.append_stmt("import dbus")
        self.class_code.append_stmt("\n")
        self.class_code.append_stmt("\n")
        self.class_code.append_stmt("class " + self.className + "(dbus.service.Object):")
        self.class_code.indent()
        
        ## Overload of __init__ method 
        self.def_method("__init__")
        self.add_method("bus, callback=None, objName='/sample', busName='org.cloudeebus'")
        self.add_stmt("self.bus = bus")
        self.add_stmt("self.objName = objName")
        self.add_stmt("self.callback = callback")        
        self.add_stmt("dbus.service.Object.__init__(self, conn=bus, object_path=objName, bus_name=busName)")
        self.end_method()
               
    def createDBusServiceFromXML(self, xml):
        self.parser = XMLParser(target=self.xmlCB)
        self.parser.feed(xml)
        self.parser.close()
    
    def set_interface(self, ifName):
        self.ifName = ifName
        
    def def_method(self, methodName):
        self.methodToAdd = methodName
        self.signalToAdd = None
        self.args_str = str()
        self.signature = {}
        self.signature['name'] = str()
        self.signature['in'] = str()                
        self.signature['out'] = str()                        

    def def_signal(self, signalName):
        self.methodToAdd = None
        self.signalToAdd = signalName
        self.args_str = str()
        self.signature = {}
        self.signature['name'] = str()
        self.signature['in'] = str()                
        self.signature['out'] = str()                        

    def add_signature(self, name, direction, signature):
        if (direction == 'in'):
            self.signature['in'] += signature
            if (self.signature['name'] != str()):
                self.signature['name'] += ", "
            self.signature['name'] += name
        if (direction == 'out'):
            self.signature['out'] = signature                        
        
    def add_method(self, args = None, async_success_cb = None, async_err_cb = None):
        async_cb_str = str()
        if (self.methodToAdd != None):
            name = self.methodToAdd
        else:
            name = self.signalToAdd
        if (args != None):
            self.args_str = args
        if (async_success_cb != None):
            async_cb_str = async_success_cb
        if (async_err_cb != None):
            if (async_cb_str != str()):
                async_cb_str += ", "
            async_cb_str += async_err_cb
                        
        parameters = self.args_str
        if (async_cb_str != str()):
            if (parameters != str()):
                parameters += ", "
            parameters +=async_cb_str       
        
        if (parameters != str()):
            self.class_code.append_stmt("def " + name + "(self, %s):" % parameters)               
        else:
            self.class_code.append_stmt("def " + name + "(self):")
        self.class_code.indent()
        
    def end_method(self):
        self.class_code.append_stmt("\n")
        self.class_code.append_stmt("\n")        
        self.class_code.dedent()
        
    def add_dbus_method(self):
        decorator = '@dbus.service.method("' + self.ifName + '"'
        if (self.signature.has_key('in') and self.signature['in'] != str()):
                decorator += ", in_signature='" + self.signature['in'] + "'"
        if (self.signature.has_key('out') and self.signature['out'] != str()):
                decorator += ", out_signature='" + self.signature['out'] + "'"
        decorator += ", async_callbacks=('dbus_async_cb', 'dbus_async_err_cb')"            
        decorator += ")"
        self.class_code.append_stmt(decorator)
        if (self.signature.has_key('name') and self.signature['name'] != str()):
            self.add_method(self.signature['name'], async_success_cb='dbus_async_cb', async_err_cb='dbus_async_err_cb')
        else:
            self.add_method(async_success_cb='dbus_async_cb', async_err_cb='dbus_async_err_cb')

    def add_dbus_signal(self):
        decorator = '@dbus.service.signal("' + self.ifName + '"'
        if (self.signature.has_key('in') and self.signature['in'] != str()):
                decorator += ", signature='" + self.signature['in'] + "'"
        decorator += ")"            
        self.class_code.append_stmt(decorator)
        if (self.signature.has_key('name') and self.signature['name'] != str()):
            self.add_method(self.signature['name'])
        else:
            self.add_method()

    def add_body_method(self):
        if (self.methodToAdd != None):
            self.class_code.append_stmt("print 'In " + self.methodToAdd + "()'")
            if (self.args_str != str()):
                self.class_code.append_stmt("self.callback('" + self.methodToAdd + "', dbus_async_cb, dbus_async_err_cb, %s)" % self.args_str)
            else:        
                self.class_code.append_stmt("self.callback('" + self.methodToAdd + "', dbus_async_cb, dbus_async_err_cb)")

    def add_body_signal(self):
        self.class_code.append_stmt("return") ## TODO: Remove and fix with code ad hoc
        self.class_code.append_stmt("\n")

    def add_stmt(self, stmt) :
        self.class_code.append_stmt(stmt)
        
    def declare(self) :
        self.class_code.compile()
        exec(self.class_code.exec_string, self.globalCtx, self.localCtx)
     
    def __str__(self) :
        return self.class_code.exec_string

    # p : Since it is often useful to be able to look at the code
    # that is generated interactively, this function provides
    # a shorthand for "print str(some_exec_code_instance)", which
    # gives a reasonable nice look at the contents of the
    # exec_code object.
    def p(self) :
        print str(self)



###############################################################################
class CloudeebusService:
    '''
    support for sending DBus messages and registering for DBus signals
    '''
    def __init__(self, permissions):
        self.permissions = permissions;
        self.proxyObjects = {}
        self.proxyMethods = {}
        self.pendingCalls = []
        self.dynDBusClasses = {} # DBus class source code generated dynamically (a list because one by classname)
        self.services = {}  # DBus service created
        self.serviceAgents = {} # Instantiated DBus class previously generated dynamically, for now, one by classname
        self.servicePendingCalls = {} # JS methods called (and waiting for a Success/error response), containing 'methodId', (successCB, errorCB)


    def proxyObject(self, busName, serviceName, objectName):
        '''
        object hash id as busName#serviceName#objectName
        '''
        id = "#".join([busName, serviceName, objectName])
        if not self.proxyObjects.has_key(id):
            if not OPENDOOR:
                # check permissions, array.index throws exception
                self.permissions.index(serviceName)
            bus = cache.dbusConnexion(busName)
            self.proxyObjects[id] = bus.get_object(serviceName, objectName)
        return self.proxyObjects[id]


    def proxyMethod(self, busName, serviceName, objectName, interfaceName, methodName):
        '''
        method hash id as busName#serviceName#objectName#interfaceName#methodName
        '''
        id = "#".join([busName, serviceName, objectName, interfaceName, methodName])
        if not self.proxyMethods.has_key(id):
            obj = self.proxyObject(busName, serviceName, objectName)
            self.proxyMethods[id] = obj.get_dbus_method(methodName, interfaceName)
        return self.proxyMethods[id]


    @exportRpc
    def dbusRegister(self, list):
        '''
        arguments: bus, sender, object, interface, signal
        '''
        if len(list) < 5:
            raise Exception("Error: expected arguments: bus, sender, object, interface, signal)")
        
        if not OPENDOOR:
            # check permissions, array.index throws exception
            self.permissions.index(list[1])
        
        # check if a handler exists
        sigId = "#".join(list)
        if cache.signalHandlers.has_key(sigId):
            return sigId
        
        # create a handler that will publish the signal
        dbusSignalHandler = DbusSignalHandler(*list)
        cache.signalHandlers[sigId] = dbusSignalHandler
        
        return dbusSignalHandler.id


    @exportRpc
    def dbusSend(self, list):
        '''
        arguments: bus, destination, object, interface, message, [args]
        '''
        # clear pending calls
        for call in self.pendingCalls:
            if not call.pending:
                self.pendingCalls.remove(call)
        
        if len(list) < 5:
            raise Exception("Error: expected arguments: bus, destination, object, interface, message, [args])")
        
        # parse JSON arg list
        args = []
        if len(list) == 6:
            args = json.loads(list[5])
        
        # get dbus proxy method
        method = self.proxyMethod(*list[0:5])
        
        # use a deferred call handler to manage dbus results
        dbusCallHandler = DbusCallHandler(method, args)
        self.pendingCalls.append(dbusCallHandler)
        return dbusCallHandler.callMethod()


    @exportRpc
    def returnMethod(self, list):
        '''
        arguments: methodId, success (=true, error otherwise), result (to return)
        '''
        methodId = list[0]
        success = list[1]
        result = list[2]
        if (self.servicePendingCalls.has_key(methodId)):
            cb = self.servicePendingCalls[methodId]
            if (success):                
                successCB = cb["successCB"]
                if (result != None):
                    successCB(result)
                else:
                    successCB()                    
            else:     
                errorCB = cb["errorCB"]        
                if (result != None):
                    errorCB(result)
                else:
                    errorCB()
            self.servicePendingCalls[methodId] = None
        else:
            print "No methodID %s  !!" % (methodId)  

    def jsonEncodeTupleKeyDict(self, data):
        ndict = dict()
        # creates new dictionary with the original tuple converted to json string
        dataLen = len(data)
        string = ""
        for index in range(dataLen):
            for key in data[index]:
                value = data[index][key]
                print "key=" + key
                print "value=" + str(value)
                nkey = str(key)
                nvalue = ""
                print "JSON key=" + nkey
                if (isinstance(value, dbus.Array)):
                    # Searching dbus byte in array...
                    ValueLen = len(value)
                    nvalue = []
                    for indexValue in range(ValueLen):
                        a = value[indexValue]
                        if (isinstance(a, dbus.Byte)):
                            a = int(value[indexValue])
                            nvalue.append(a)
                        else:
                            nvalue = str(value[indexValue])
                            
                print "JSON value=" + str(nvalue)                
                ndict[nkey] =  nvalue

        return ndict

    def srvCB(self, name, async_succes_cb, async_error_cb, *args):
        methodId = self.srvName + "#" + self.agentObjectPath + "#" + name
        cb = { 'successCB': async_succes_cb, 
               'errorCB': async_error_cb}
        self.servicePendingCalls[methodId] = cb

        print "Received args=%s" % (args)                     
        try:               
            print "factory.dispatch(methodId=%s, args=%s)" % (methodId, json.dumps(args))                     
            factory.dispatch(methodId, json.dumps(args))
            return
        except Exception, e :
            print "Error=%s" % (str(e))
            
        print "Trying to decode dbus.Dictionnary..."
        try:
            params = self.jsonEncodeTupleKeyDict(args)                
            print "factory.dispatch(methodId=%s, args=%s)" % (methodId, params)                     
            factory.dispatch(methodId, params)
            return
        except Exception, e :
            print "Error=%s" % (str(e))
                    
        print "Trying to pass args as string..."
        try:               
            print "factory.dispatch(methodId=%s, args=%s)" % (methodId, str(args))                     
            factory.dispatch(methodId, str(args))
            return
        except Exception, e :
            print "Error=%s" % (str(e))
                    
    @exportRpc
    def serviceAdd(self, list):
        '''
        arguments: busName, srvName
        '''
        busName = list[0]
        self.bus =  cache.dbusConnexion( busName['name'] )
        self.srvName = list[1]
        if (self.services.has_key(self.srvName) == False):            
            self.services[self.srvName] = dbus.service.BusName(name = self.srvName, bus = self.bus)
            return self.srvName

    @exportRpc
    def serviceRelease(self, list):
        '''
        arguments: busName, srvName
        '''
        busName = list[0]
        self.bus =  cache.dbusConnexion( busName['name'] )
        self.srvName = list[1]
        if (self.services.has_key(self.srvName) == True):
            exe_str = "self.services['" + self.srvName +"']"
            exec (exe_str, globals(), locals())
            return self.srvName
        else:
            raise Exception(self.srvName + " do not exist")
                   
    @exportRpc
    def serviceAddAgent(self, list):
        '''
        arguments: objectPath, xmlTemplate
        '''
        self.agentObjectPath = list[0]
        xmlTemplate = list[1]
        self.className = re.sub('/', '_', self.agentObjectPath[1:])
        if (self.dynDBusClasses.has_key(self.className) == False):
            self.dynDBusClasses[self.className] = dynDBusClass(self.className, globals(), locals())
            self.dynDBusClasses[self.className].createDBusServiceFromXML(xmlTemplate)
            
            # For Debug only
            if (1):
                if (1): ## Force deletion
                    if os.access('./MyDbusClass.py', os.R_OK) == True:
                        os.remove('./MyDbusClass.py')
                    
                    if os.access('./MyDbusClass.py', os.R_OK) == False:
                        f = open('./MyDbusClass.py', 'w')
                        f.write(self.dynDBusClasses[self.className].class_code.exec_string)
                        f.close()
#                self.dynDBusClass[className].p()
                self.dynDBusClasses[self.className].declare()
            
            if (self.serviceAgents.has_key(self.className) == False):
                exe_str = "self.serviceAgents['" + self.className +"'] = " + self.className + "(self.bus, callback=self.srvCB, objName=self.agentObjectPath, busName=self.srvName)"
                exec (exe_str, globals(), locals())
                return (self.agentObjectPath)
        else:
            raise Exception(self.agentObjectPath + " already exist !!")
                    
    @exportRpc
    def serviceDelAgent(self, list):
        '''
        arguments: objectPath, xmlTemplate
        '''
        agentObjectPath = list[0]
        className = re.sub('/', '_', agentObjectPath[1:])

        if (self.serviceAgents.has_key(className)):            
            exe_str = "self.serviceAgents['" + className +"'] = None"
            exec (exe_str, globals(), locals())
            return (self.className)
        else:
            raise Exception(agentObjectPath + "doesn't exist!")
                    
    @exportRpc
    def getVersion(self):
        '''
        return current version string
        '''
        return VERSION



###############################################################################
class CloudeebusServerProtocol(WampCraServerProtocol):
    '''
    connexion and session authentication management
    '''
    
    def onSessionOpen(self):
        # CRA authentication options
        self.clientAuthTimeout = 0
        self.clientAuthAllowAnonymous = OPENDOOR
        # CRA authentication init
        WampCraServerProtocol.onSessionOpen(self)
    
    
    def getAuthPermissions(self, key, extra):
        return json.loads(extra.get("permissions", "[]"))
    
    
    def getAuthSecret(self, key):
        secret = CREDENTIALS.get(key, None)
        if secret is None:
            return None
        # secret must be of str type to be hashed
        return secret.encode('utf-8')
    

    def onAuthenticated(self, key, permissions):
        if not OPENDOOR:
            # check authentication key
            if key is None:
                raise Exception("Authentication failed")
            # check permissions, array.index throws exception
            for req in permissions:
                WHITELIST.index(req)
        # create cloudeebus service instance
        self.cloudeebusService = CloudeebusService(permissions)
        # register it for RPC
        self.registerForRpc(self.cloudeebusService)
        # register for Publish / Subscribe
        self.registerForPubSub("", True)
    
    
    def connectionLost(self, reason):
        WampCraServerProtocol.connectionLost(self, reason)
        if factory.getConnectionCount() == 0:
            cache.reset()



###############################################################################

if __name__ == '__main__':
    
    cache = DbusCache()

    parser = argparse.ArgumentParser(description='Javascript DBus bridge.')
    parser.add_argument('-v', '--version', action='store_true', 
        help='print version and exit')
    parser.add_argument('-d', '--debug', action='store_true', 
        help='log debug info on standard output')
    parser.add_argument('-o', '--opendoor', action='store_true',
        help='allow anonymous access to all services')
    parser.add_argument('-p', '--port', default='9000',
        help='port number')
    parser.add_argument('-c', '--credentials',
        help='path to credentials file')
    parser.add_argument('-w', '--whitelist',
        help='path to whitelist file')
    
    args = parser.parse_args(sys.argv[1:])

    if args.version:
        print("Cloudeebus version " + VERSION)
        exit(0)
    
    if args.debug:
        log.startLogging(sys.stdout)
    
    OPENDOOR = args.opendoor
    
    if args.credentials:
        jfile = open(args.credentials)
        CREDENTIALS = json.load(jfile)
        jfile.close()
    
    if args.whitelist:
        jfile = open(args.whitelist)
        WHITELIST = json.load(jfile)
        jfile.close()
    
    uri = "ws://localhost:" + args.port
    
    factory = WampServerFactory(uri, debugWamp = args.debug)
    factory.protocol = CloudeebusServerProtocol
    factory.setProtocolOptions(allowHixie76 = True)
    
    listenWS(factory)
    
    DBusGMainLoop(set_as_default=True)
    
    reactor.run()
