import json
import os
import ast
# Importing internal libraries
from my_libs.helper_module import helper_function 
from my_libs.another_helper_module import another_helper_function
# Importing external libraries
import numpy as np

def lambda_handler(event, context):
    # Run different types of helper functions
    print(helper_function())
    print(another_helper_function())
    print(f"Calculations performed by a call to the numpy library: {helper_using_numpy_library()}")
    # Return from Lambda
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'This is the data returned by the container running within Lambda!'
        })
    }

def helper_using_numpy_library():
    # Retrieve the NumPy array from the environment
    input_str = os.getenv("NUMPY_ARRAY")
    if input_str is None:
        return ("Environment Variable NUMPY_ARRAY does not exist")
    if input_str == "":
        return ("No value provided in Environment Variable NUMPY_ARRAY")
    # TODO: Add regex to validate the value provided

    input_array = ast.literal_eval(input_str)
    print(f"Value of NUMPY_ARRAY Environment Variable: {input_array}")
    data = np.array(input_array)
    # Reshape the array into a 2D array (matrix)
    data_reshaped = data.reshape(2, 5)
    # Perform matrix multiplication
    matrix_result = np.dot(data_reshaped, data_reshaped.T)  # Matrix multiplication of the array with its transpose

    return(matrix_result)