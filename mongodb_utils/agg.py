'''

@author: jdrumgoole
'''
import pprint
from datetime import datetime
from collections import OrderedDict
from mongodb_utils.nested_dict import Nested_Dict

import pymongo
import csv
import contextlib
import sys
import json
class Sorter( object ):
    '''
    Required for ordered sorting of fields as python dictionaries do not 
    guarantee to maintain insertion order
    '''
    
    def __init__(self,  **kwargs ):
        '''
        parameters are key="asending" or key="descending"
        '''
        self._sorted = {}
        self._sorted[ "$sort"] = OrderedDict()

        self.add( kwargs )
        
    def add(self, sorts ):
        for  k,v in sorts.iteritems():
            self.add_sort( k, v )
            
    def sort_fields(self):
        return self._sorted[ "$sort"].keys()
    
    def sort_items(self):
        return self._sorted[ "$sort"].items()
    
    def sort_directions(self):
        return self._sorted[ "$sort"].values()
    
    def sorts(self):
        return self._sorted
            
    def add_sort(self, field, sortOrder = pymongo.ASCENDING ):
        if sortOrder in [ pymongo.ASCENDING, pymongo.DESCENDING ]:
            self._sorted[ "$sort" ][ field ] = sortOrder
        else:
            raise ValueError( "Invalid sort order must be pymongo.ASCENDING or pymongo.DESCENDING")
    
    def __call__(self):
        return self._sorted
        
    def __str__(self):
        return str( self._sorted )
    
    def __repr__(self):
        return self.__str__()
        
class CursorFormatter( object ):
    '''
    Take a mongodb Agg object and call aggregate on it.
    IF root is "-" send the output to stdout.
    
    If root is a file name output the content to that file.
    '''
        
    def __init__(self, agg, filename="", formatter="json",  results=[] ):
        '''
        Data from cursor
        output to <filename>suffix.ext.
        '''
        
        if not isinstance( agg, Agg ):
            raise ValueError( "aggregate argument to CursorFormatter is not of class Agg")
        
        self._agg = agg
        self._format = formatter
        self._filename = filename
        self._results = results
        
    def results(self):
        return self._results 
    
    @contextlib.contextmanager
    def _smart_open(self, filename=None):
        if filename and filename != '-':
            fh = open(filename, 'wb' )
        else: 
            fh = sys.stdout
    
        try:
            yield fh
        finally:
            if fh is not sys.stdout:
                fh.close()
                


    @staticmethod
    def dateMapField( doc, field, time_format=None):
        '''
        Given a field that contains a datetime 
        '''
        
        if time_format is None:
            time_format = "%d-%b-%Y %H:%M"
        d = Nested_Dict( doc )
        value = d.get_value(  field )
        if isinstance( value, datetime ):
            d.set_value( field, value.strftime( time_format ) )
        else:
            raise ValueError( "Field '%s' is not a datetime field")

        return d.dict_value()
       
    @staticmethod
    def fieldMapper( doc, fields ): 
        '''
        Copy all fields from doc to a new doc and return that doc
        '''
        
        
        if fields is None or len( fields ) == 0 :
            return doc
        
        newDoc = Nested_Dict( {} )
        oldDoc = Nested_Dict( doc )

        
        for i in fields:
            if oldDoc.has_key( i ):
                #print( "doc: %s" % doc )
                #print( "i: %s" %i )
                newDoc.set_value( i, oldDoc.get_value(  i ))
            else:
                raise ValueError( "No such key '%s' in %s" % (i, doc )) 
        return newDoc.dict_value() 
    
    @staticmethod
    def dateMapper( doc, datemap, time_format=None ):
        '''
        For all the fields in "datemap" find that key in doc and map the datetime object to 
        a strftime string.
        '''
        if datemap:
            for i in datemap :
                CursorFormatter.dateMapField( doc, i, time_format=None )
        return doc
                
    def printCSVCursor( self, c, fieldnames, datemap, time_format=None):
        '''
        Output CSV format. items are separated by commas.
        '''
            
        with self._smart_open( self._filename ) as output :
            writer = csv.DictWriter( output, fieldnames = fieldnames)
            writer.writeheader()
            count = 0
            for i in c:
                self._results.append( i )
                count = count + 1
                d = CursorFormatter.fieldMapper( i, fieldnames )
                d = CursorFormatter.dateMapper( d , datemap,time_format )

                x={}
                for k,v in d.items():

                    if type( v ) is unicode :
                        x[ k ] = v
                    else:
                        x[ k ] = str( v ).encode( 'utf8')
                    
                writer.writerow( {k:v.encode('utf8') for k,v in x.items()} ) 
            
        return count

    
    def printJSONCursor( self, c,fieldnames, datemap, time_format=None ):
        '''
        Output plain JSON objects.
        '''
        
        count = 0 

        with self._smart_open( self._filename ) as output:
            for i in c :
                #print( "processing: %s" % i )
                #print( "fieldnames: %s" % fieldnames )
                self._results.append( i )
                d = CursorFormatter.fieldMapper( i, fieldnames )
                #print( "processing fieldmapper: %s" % d )
                CursorFormatter.dateMapper( d, datemap )
                pprint.pprint( d, output )
                count = count + 1

        return count

    def printCursor( self, c, fieldnames=None, datemap=None, time_format=None  ):
        '''
        Output a cursor to a filename or stdout if filename is "-".
        fmt defines whether we output CSV or JSON.
        '''
    
        if self._format == 'csv' :
            count = self.printCSVCursor( c, fieldnames, datemap, time_format )
        else:
            count = self.printJSONCursor( c,  fieldnames, datemap, time_format )
            
        return count 
    
    def output(self, fieldNames=None, datemap=None, time_format=None, aggregate=True, limit=None ):
        '''
        Output all fields using the fieldNames list. for fields in the list datemap indicates the field must
        be date
        '''
        if self._filename != "-" : 
            print( "Writing to '%s'" % self._filename )
        
        if limit :
            self._agg.addLimit( limit )
        if aggregate :
            print( self._agg )
        count = self.printCursor( self._agg.aggregate(), fieldNames, datemap, time_format )
        print( "Wrote %i records" % count )
        
class Agg(object):
    '''
    A wrapper class for the MongoDB Aggregation framework (MongoDB 3.2)
    '''

    def __init__(self, collection, formatter="json" ):
        '''
        Constructor json or python for format.
        '''
        self._collection   = collection
        self._hasDollarOut = False
        self._cursor       = None
        self._elapsed      = None
        self._formatter = formatter
        self.clear()
    
    @staticmethod
    def __limit( size ):
        return  { "$limit" : size }
    
    @staticmethod
    def __sample( sampleSize ):
        return { "$sample" : { "$size" : sampleSize }}
        
    @staticmethod
    def __match( matcher ):
        Agg.__typeCheckDict( matcher )
        return { "$match" : matcher  }
     
    @staticmethod
    def __project( projector ):
        Agg.__typeCheckDict( projector )
        return { "$project" : projector }
    
     
    @staticmethod
    def __group( grouper ):
        Agg.__typeCheckDict( grouper )
        return { "$group" : grouper }
    
    @staticmethod
    def __unwind( unwinder ):
        #Agg.__typeCheckDict( unwinder )
        return { "$unwind" : unwinder }
    
    @staticmethod
    def __sort( sorter ):
        # we typecheck higher up the stack
        return { "$sort" : sorter }
    
    @staticmethod
    def __out( output ):
        return { "$out" : output }
    
    @staticmethod
    def __typeCheckDict( val ):
        if not isinstance( val, dict ):
            t = type( val )
            raise ValueError( "Parameters must be dict objects: %s is a %s object " % ( val, t ))
        

    def __hasDollarOutCheck( self, op ):
        if self._hasDollarOut :
            raise ValueError( "Cannot have more aggregation pipeline operations after $out: operation '%s'" % op )
        
    def addLimit(self, size=None):
        
        if size is None :
            return self
        
        self.__hasDollarOutCheck( "limit: %i" % size )
        self._agg.append( Agg.__limit( size ))
        
        return self
    
    def addSample(self, size=100):
        
        self.__hasDollarOutCheck( "sample: %i" % size )
        self._agg.append( Agg.__sample( size ))
        
        return self
    
    def addMatch(self, matcher ):

        self.__hasDollarOutCheck( "match: %s" % matcher )
        self._agg.append( Agg.__match( matcher ))
        
        return self
        
    def addProject(self, projector ):
        
        self.__hasDollarOutCheck( "project: %s" % projector )
        self._agg.append( Agg.__project( projector ))
        
        return self

    def addGroup(self, grouper ):
        
        self.__hasDollarOutCheck( "group: %s" % grouper )
        self._agg.append( Agg.__group( grouper ))
        
        return self
    
    def addSort(self, sorter ):
        '''
        Sorter can be a single dict or a list of dicts.
        '''
        
        self.__hasDollarOutCheck( "$sort: %s" % sorter )
        
        if isinstance( sorter, Sorter) :
            self._agg.append( sorter())
        else:
            raise ValueError( "Parameter to addSort must of of class Sorter (type is '%s'" % type( sorter ))
        return self

    def addUnwind(self, unwinder ):
        
        self.__hasDollarOutCheck( "$unwind: %s" % unwinder )
        self._agg.append( Agg.__unwind( unwinder ))
        
        return self
    
    def addOut(self, output=None ):
        
        if output is None :
            return self
        
        if self._hasDollarOut :
            raise ValueError( "Aggregation already has $out defined: %s" % self._agg )
        else:
            self._agg.append( Agg.__out( output ))
            self._hasDollarOut = True
            
        return self
    
    def clear(self):
        self._agg = []
        self._hasDollarOut = False
        self._elapsed = 0
        self._cursor = None
        
            
        return self
    
    def echo(self):
        print( self._agg )
        return self
    
    def formatter(self, output="json"):
        
        if output == "json" :
            return self.json_format()
        elif output == "python" :
            return self.python_format()
        else:
            raise ValueError( "bad parmeter : output : %s" %  output )
    @staticmethod
    def json_serial(obj):
        """JSON serializer for objects not serializable by default json code"""

        if isinstance(obj, datetime):
            serial = "ISODate( " + obj.isoformat() + " )" 
            return serial
        raise TypeError ("Type not serializable")

    def json_format(self):
        agg = "db." + self._collection.name + ".aggregate([\n"
        for i in self._agg :
            #            agg = agg + pprint.pformat( i ) + ",\n" 
            agg = agg + json.dumps( i, default=Agg.json_serial ) + ",\n"
       
        if agg.endswith( ",\n"):
            agg = agg[:-2]
            
        return agg + '])\n'
    
    def python_format(self):
        agg = "db." + self._collection.name + ".aggregate( [\n"
        for i in self._agg :
            agg = agg + pprint.pformat( i ) + ",\n"
            
        if agg.endswith( ",\n"):
            agg = agg[:-2]
            
        return agg + '])\n'
        
    def __repr__(self):
        
        return self.formatter( self._formatter )
    
    def __str__(self):
        return self.__repr__()
    
    def addRangeMatch( self, date_field, start=None, end=None ):
    
        query = None
        if start and end :
            query = { date_field : { "$gte" : start, "$lte" : end }}
        elif start :
            query = { date_field : { "$gte" : start  }}
        elif end :
            query ={ date_field : { "$lte" : end  }}
        
        if query:
            self.addMatch( query )
            
        return self
    
    @staticmethod
    def cond( boolean_expr, thenClause, elseClause ):  #$cond: { if: { $gte: [ "$qty", 250 ] }, then: 30, else: 20 }
        return { "$cond" : { "if" : boolean_expr, "then" : thenClause, "else" :  elseClause }}
    
    @staticmethod
    def ifNull( null_value, non_null_value ):
        return { "$ifNull" : [ null_value, non_null_value ] }
    

    
    def cursor(self):
        return self._cursor 
    
    def elapsed(self):
        return self._elapsed 
    
    def aggregate(self ):
        
        start = datetime.utcnow()
        self._cursor =  self._collection.aggregate( self._agg )
        finish = datetime.utcnow()
        
        self._elapsed = finish - start
        
        return self._cursor
    
    def __call__(self ):
        
        return self.aggregate()

    def tee(self, output ):
        '''
        Iterator over the aggregator and produce a copy in output
        '''

        for i in self.aggregate():
            output.append( i )
            yield i