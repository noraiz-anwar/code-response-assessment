/**
 Interface for response (submission) view.

 Args:
 element (DOM element): The DOM element representing the XBlock.
 server (OpenAssessment.Server): The interface to the XBlock server.
 fileUploader (OpenAssessment.FileUploader): File uploader instance.
 baseView (OpenAssessment.BaseView): Container view.
 data (Object): The data object passed from XBlock backend.

 Returns:
 OpenAssessment.ResponseView
 **/

OpenAssessment.ResponseView = function (element, server, fileUploader, baseView, data) {
    this.element = element;
    this.server = server;
    this.fileUploader = fileUploader;
    this.baseView = baseView;
    this.savedResponse = [];
    this.textResponse = 'required';
    this.showFileUplaodCode = false;
    this.fileUploadResponse = '';
    this.files = null;
    this.filesDescriptions = [];
    this.filesType = null;
    this.lastChangeTime = Date.now();
    this.errorOnLastSave = false;
    this.autoSaveTimerId = null;
    this.data = data;
    this.filesUploaded = false;
    this.announceStatus = false;
    this.isRendering = false;
    this.dateFactory = new OpenAssessment.DateTimeFactory(this.element);
    this.codeEditor = null;
    this.languageError = false;
};

OpenAssessment.ResponseView.prototype = {

    // Milliseconds between checks for whether we should autosave.
    AUTO_SAVE_POLL_INTERVAL: 30000,

    // Required delay after the user changes a response or a save occurs
    // before we can autosave.
    AUTO_SAVE_WAIT: 30000,

    // Maximum size (20 * 2^20 bytes, approx. 20MB) for all attached files.
    MAX_FILES_SIZE: 20971520,

    // For user-facing upload limit text.
    MAX_FILES_MB: 20,

    UNSAVED_WARNING_KEY: 'learner-response',

    /**
     Load the response (submission) view.
     **/
    load: function (usageID) {
        var view = this;
        var stepID = '.step--response';
        var focusID = '[id=\'oa_response_' + usageID + '\']';

        view.isRendering = true;
        this.server.render('submission').done(
            function (html) {
                // Load the HTML and install event handlers
                $(stepID, view.element).replaceWith(html);
                view.server.renderLatex($(stepID, view.element));
                // Editor should be setup before registering all the handlers
                view.setupCodeEditor();
                view.installHandlers();
                view.setAutoSaveEnabled(true);
                view.isRendering = false;
                view.baseView.announceStatusChangeToSRandFocus(stepID, usageID, false, view, focusID);
                view.announceStatus = false;
                view.dateFactory.apply();
            }
        ).fail(function () {
            view.baseView.showLoadError('response');
        });
    },

    /**
     Install event handlers for the view.
     **/
    installHandlers: function () {
        var sel = $('.step--response', this.element);
        var view = this;
        var uploadType = '';
        if (sel.find('.submission__answer__display__file').length) {
            uploadType = sel.find('.submission__answer__display__file').data('upload-type');
        }
        // Install a click handler for collapse/expand
        this.baseView.setUpCollapseExpand(sel);

        // Install change handler for textarea (to enable submission button)
        this.savedResponse = this.response('load');
        var handleChange = function () { view.handleResponseChanged(); };
        var langChange = function () { view.handleLanguageSelectionChanged(); };

        if (view.codeEditor != null) {
            view.codeEditor.on('change keyup drop paste', handleChange);
        }

        // Adding on change handler for dropdown
        sel.find('select#submission__answer__language').on('change', langChange);

        var handlePrepareUpload = function (eventData) { view.prepareUpload(eventData.target.files, uploadType); };
        sel.find('input[type=file]').on('change', handlePrepareUpload);

        var submit = $('.step--response__submit', this.element);
        this.textResponse = $(submit).attr('text_response');
        var editor_textarea = $('.response__submission .submission__answer__part__text__value', this.element);
        this.showFileUplaodCode = $(editor_textarea).attr('show_file_read_code');
        this.fileUploadResponse = $(submit).attr('file_upload_response');

        // Install a click handler for submission
        sel.find('.step--response__submit').click(
            function (eventObject) {
                // Override default form submission
                eventObject.preventDefault();
                view.submit();
            }
        );

        // Install a click handler for the save button
        sel.find('.submission__save').click(
            function (eventObject) {
                // Override default form submission
                eventObject.preventDefault();
                view.save();
            }
        );

        // Install click handler for the preview button
        this.baseView.bindLatexPreview(sel);

        // Install a click handler for the save button
        sel.find('.file__upload').click(
            function (eventObject) {
                // Override default form submission
                eventObject.preventDefault();
                var previouslyUploadedFiles = sel.find('.submission__answer__file').length ? true : false;
                $('.submission__answer__display__file', view.element).removeClass('is--hidden');
                if (view.hasAllUploadFiles()) {
                    if (previouslyUploadedFiles) {
                        // eslint-disable-next-line max-len
                        var msg = gettext('After you upload new files all your previously uploaded files will be overwritten. Continue?');
                        if (confirm(msg)) {
                            view.uploadFiles();
                        }
                    } else {
                        view.uploadFiles();
                    }
                }
            }
        );
    },

    /*
    Get text areas
     */
    getPrompts: function () {
        return $('.response__submission .submission__answer__part__text__value', this.element);
    },

    createTextArea: function (value) {
        var $elem = $('<p></p>');
        $elem.text(value);
        $elem.addClass('output_text_area');
        return $elem;
    },

    errorTextArea: function (value) {
        var $elem = $('<p></p>');
        $elem.text(value);
        $elem.addClass('output_error_text_area');
        return $elem;
    },

    createOutputHeader: function (value) {
        return "<p class='output_text_area'>" + value + "</p>"
    },

    /*
     Setup code editor in place of textarea
    */
    setupCodeEditor: function () {
        var textArea = this.getPrompts()[0];
        if (textArea != null) {
            this.codeEditor = window.CodeMirror.fromTextArea(textArea, {
                lineNumbers: true,
                showCursorWhenSelecting: true,
                inputStyle: "contenteditable",
                smartIndent: true,
                indentWithTabs: true,
                indentUnit: 4
            }
            );
            this.codeEditor.setSize(null, 600);
            this.updateEditorMode(this.getLanguage());
        }
    },

    /*
    Renders which test number failed and which has passed
    */
    showTestCaseResult: function (test_results) {

        var $table, $row, style;
        var header_keys = ["Test Input", "Your Output", "Expected Output"];
        var data_keys = ["test_input", "actual_output", "expected_output"];

        // Setup Table
        $table = $("<table>");
        $table.addClass("results_table");

        // Setup Table header HTML and values
        $table.append('<thead>');
        $table.find('thead').append("<tr>");
        $row = $table.find("thead > tr:last");
        for (var idx in header_keys) {
            $row.append("<th>");
            $row.find("th:last").append(this.createOutputHeader(header_keys[idx]));
        }

        // Setup Table body HTML and values
        $table.append('<tbody>');
        for (var key in test_results) {
            $table.find('tbody').append("<tr>");
            $row = $table.find("tbody > tr:last");

            style = "rgba(205, 0, 0, 0.3)";
            if (test_results[key]['correct'] == true) {
                style = "rgba(0, 205, 0, 0.3)";
            }
            $row.css('background', style);

            for (var index in data_keys) {
                $row.append("<td>");
                $row.find("td:last").append(this.createTextArea(test_results[key][data_keys[index]]));
            }
        }

        // Update the summary element with created table
        $("#test_case_status_result", this.element).html($table);
    },

    /*
    Render the code output for the design problems
    */
    showExecutionResults: function (output) {
        var $header, $content;
        $header = this.getExecutionResultHeader();
        $content = $('<p></p>')
        $content.text(output);
        $content.addClass('execution_output');
        $content = $header.add($content);
        $("#test_case_status_result", this.element).html($content);
    },

    /*
    Render the code execution errors for the design problems
    */
    showExecutionError: function (error) {
        var $header, $content;
        $header = this.getExecutionResultHeader();
        $content = this.errorTextArea(error);
        $content = $header.add($content);
        $("#test_case_status_result", this.element).html($content);
    },

    /*
    Create and return the header for design problem execution
    */
    getExecutionResultHeader: function () {
        var $header = $('<h2></h2>');
        $header.text(gettext("Code Execution Result"));
        $header.css('color', 'black');
        return $header
    },

    /*
    Add the HTML to show how many test cases passed from the total number
    */
    showResultSummary: function (publicResults, privateResults) {
        var $summary = $("<div>");
        $summary.addClass('results_summary');
        $summary.append(
            "<p><strong>Sample Test Cases Result: "
            + publicResults.correct + "/"
            + publicResults.total
            + "</strong></p>"
        );
        if (privateResults) {
            $summary.append(
                "<p><strong>Hidden Test Cases Result: "
                + privateResults.correct + "/"
                + privateResults.total
                + "</strong></p>"
            );

        }
        $summary.append("</div>")
        $("#test_cases_summary", this.element).html($summary);
    },

    /*
    Clear the summary HTML
    */
    clearResultSummary: function () {
        $("#test_cases_summary").html("");
    },

    /*
    Displays a textbox containing the code error
    */
    showRunError: function (error) {
        $("#test_case_status_result", this.element).html(this.errorTextArea(error));
    },

    /*
    Displays a textbox containing the error when no language is selected
    */
    showLanguageError: function (error) {
        this.languageError = true;
        this.showRunError(error);
    },

    /*
    Clear the no language error
    */
    clearLanguageError: function () {
        if (this.languageError) {
            $("#test_case_status_result", this.element).html("");
            this.languageError = false;
        }
    },

    /*
    Show the response is either correct/incorrect based on the given value
    */
    indicateCorrectness: function (correctness) {
        if (correctness == true) {
            this.saveStatus(gettext("Code output matches the expected output"));
        }
        else {
            this.saveStatus(gettext("Code output does not match with the expected output"));
        }
    },

    /*
    Code Execution error message
    */
    indicateError: function () {
        this.saveStatus(gettext("Execution Error"));
    },

    /*
    Indicate successful code execution
    */
    indicateExecutionSuccess: function () {
        this.saveStatus(gettext("Code Execution Successful"));
    },

    /*
    Get the currently selected language from the dropdown
    */
    getLanguage: function () {
        return $("select#submission__answer__language", this.element).val();
    },

    /**
     Enable or disable autosave polling.

     Args:
     enabled (boolean): If true, start polling for whether we need to autosave.
     Otherwise, stop polling.
     **/
    setAutoSaveEnabled: function (enabled) {
        if (enabled) {
            if (this.autoSaveTimerId === null) {
                this.autoSaveTimerId = setInterval(
                    $.proxy(this.autoSave, this),
                    this.AUTO_SAVE_POLL_INTERVAL
                );
            }
        } else {
            if (this.autoSaveTimerId !== null) {
                clearInterval(this.autoSaveTimerId);
            }
        }
    },

    /**
     * Check that "submit" button could be enabled (or disabled)
     *
     * Args:
     * filesFiledIsNotBlank (boolean): used to avoid race conditions situations
     * (if files were successfully uploaded and are not displayed yet but
     * after upload last file the submit button should be available to push)
     *
     */
    checkSubmissionAbility: function (filesFiledIsNotBlank) {
        var currentResponse = this.response('save');
        var textFieldsIsNotBlank = !(Object.keys(currentResponse).forEach(function (key) {
            return $.trim(currentResponse[key]) === '';
        }));

        filesFiledIsNotBlank = filesFiledIsNotBlank || false;
        $('.submission__answer__file', this.element).each(function () {
            if (($(this).prop('tagName') === 'IMG') && ($(this).attr('src') !== '')) {
                filesFiledIsNotBlank = true;
            }
            if (($(this).prop('tagName') === 'A') && ($(this).attr('href') !== '')) {
                filesFiledIsNotBlank = true;
            }
        });
        var readyToSubmit = true;

        if ((this.textResponse === 'required') && !textFieldsIsNotBlank) {
            readyToSubmit = false;
        }
        if ((this.fileUploadResponse === 'required') && !filesFiledIsNotBlank) {
            readyToSubmit = false;
        }
        if ((this.textResponse === 'optional') && (this.fileUploadResponse === 'optional') &&
            !textFieldsIsNotBlank && !filesFiledIsNotBlank) {
            readyToSubmit = false;
        }
        this.submitEnabled(readyToSubmit);
    },

    /**
     * Check that "save" button could be enabled (or disabled)
     *
     */
    checkSaveAbility: function () {
        var currentResponse = this.response('save');
        var textFieldsIsNotBlank = !(Object.keys(currentResponse).forEach(function (key) {
            return $.trim(currentResponse[key]) === '';
        }));

        return !((this.textResponse === 'required') && !textFieldsIsNotBlank);
    },

    /**
     Enable/disable the submit button.
     Check that whether the submit button is enabled.

     Args:
     enabled (bool): If specified, set the state of the button.

     Returns:
     bool: Whether the button is enabled.

     Examples:
     >> view.submitEnabled(true);  // enable the button
     >> view.submitEnabled();  // check whether the button is enabled
     >> true
     **/
    submitEnabled: function (enabled) {
        return this.baseView.buttonEnabled('.step--response__submit', enabled);
    },

    /**
     Enable/disable the save button.
     Check whether the save button is enabled.

     Also enables/disables a beforeunload handler to warn
     users about navigating away from the page with unsaved changes.

     Args:
     enabled (bool): If specified, set the state of the button.

     Returns:
     bool: Whether the button is enabled.

     Examples:
     >> view.submitEnabled(true);  // enable the button
     >> view.submitEnabled();  // check whether the button is enabled
     >> true
     **/
    saveEnabled: function (enabled) {
        return this.baseView.buttonEnabled('.submission__save', enabled);
    },

    /**
     Enable/disable the preview button.

     Works exactly the same way as saveEnabled method.
     **/
    previewEnabled: function (enabled) {
        return this.baseView.buttonEnabled('.submission__preview', enabled);
    },
    /**
      Check if there is a file selected but not uploaded yet
      Returns:
      boolean: if we have pending files or not.
     **/
    hasPendingUploadFiles: function () {
        return this.files !== null && !this.filesUploaded;
    },
    /**
     Check if there is a selected file moved or deleted before uploading
     Returns:
     boolean: if we have deleted/moved files or not.
     **/
    hasAllUploadFiles: function () {
        for (var i = 0; i < this.files.length; i++) {
            var file = this.files[i];
            if (file.size === 0) {
                this.baseView.toggleActionError(
                    'upload',
                    gettext('Your file ' + file.name + ' has been deleted or path has been changed.'));
                this.submitEnabled(true);
                return false;
            }
        }
        return true;
    },
    /**
     Set the save status message.
     Retrieve the save status message.

     Args:
     msg (string): If specified, the message to display.

     Returns:
     string: The current status message.
     **/
    saveStatus: function (msg) {
        var sel = $('.save__submission__label', this.element);
        if (typeof msg === 'undefined') {
            return sel.text();
        } else {
            // Setting the HTML will overwrite the screen reader tag,
            // so prepend it to the message.
            var label = gettext('Status of Your Response');
            sel.html('<span class="sr">' + _.escape(label) + ':' + '</span>\n' + msg);
        }
    },

    /**
     Set the response texts.
     Retrieve the response texts.

     Args:
     texts (array of strings): If specified, the texts to set for the response.

     Returns:
     array of strings: The current response texts.
     **/
    response: function (action) {
        var editorValue;
        if (this.codeEditor != null) {
            editorValue = this.codeEditor.getValue();
        }
        else {
            editorValue = null;
        }
        return { "submission": editorValue, "language": this.getLanguage() };
    },

    /**
     Check whether the response texts have changed since the last save.

     Returns: boolean
     **/
    responseChanged: function () {
        var savedResponse = this.savedResponse;
        var currentResponse = this.response('save');
        var isResponseChanged = !Object.keys(currentResponse).every(
            key => savedResponse.hasOwnProperty(key)
                && savedResponse[key] === currentResponse[key]);
        return isResponseChanged;
    },

    /**
     Automatically save the user's response if certain conditions are met.

     Usually, this would be called by a timer (see `setAutoSaveEnabled()`).
     For testing purposes, it's useful to disable the timer
     and call this function synchronously.
     **/
    autoSave: function () {
        // We only autosave if the following conditions are met:
        // (1) The response has changed.  We don't need to keep saving the same response.
        // (2) No errors occurred on the last save.  We don't want to keep refreshing
        //      the error message in the UI.  (The user can still retry the save manually).
        if (this.responseChanged() && !this.errorOnLastSave) {
            this.autoSaveToServer();
        }
    },

    /**
    Handle if the language selection dropdown has been changed
    **/
    handleLanguageSelectionChanged: function () {
        var language = this.getLanguage();
        this.updateEditorMode(language);
        this.clearLanguageError();
        this.handleResponseChanged();
        var defaulCodes = {
            "Python": "import sys\n" +
                "\n" +
                "lines = open(sys.argv[1], 'r').readlines()\n" +
                "\n" +
                "# Write your code here.",
            "NodeJS": "const fs = require('fs');\n" +
                "\n" +
                "const args = process.argv.slice(2);\n" +
                "const fileName = args[0];\n" +
                "\n" +
                "const content = fs.readFileSync(fileName).toString();\n" +
                "const lines = content.split('\\n');\n" +
                "\n" +
                "// Write your code here.",
            "Java": "import java.io.File;\n" +
                "import java.io.FileNotFoundException;\n" +
                "import java.util.Scanner;\n" +
                "\n" +
                "\n" +
                "public class Main {\n" +
                "  public static void main(String[] args) {\n" +
                "    try {\n" +
                "      File inputFile = new File(args[0]);\n" +
                "      Scanner inputReader = new Scanner(inputFile);\n" +
                "      while (inputReader.hasNextLine()) {\n" +
                "        String line = inputReader.nextLine();\n" +
                "\n" +
                "        // Write your code here.\n" +
                "\n" +
                "      }\n" +
                "      inputReader.close();\n" +
                "    } catch (FileNotFoundException e) {\n" +
                "      System.out.println(\"An error occurred.\");\n" +
                "      e.printStackTrace();\n" +
                "    }\n" +
                "  }\n" +
                "}",
            "C++": "#include <iostream>\n" +
                "#include <fstream>\n" +
                "\n" +
                "using namespace std;\n" +
                "\n" +
                "\n" +
                "int main(int argc, char *argv[]) {\n" +
                "  ifstream inputFile(argv[1]);\n" +
                "\n" +
                "  string line = \"\";\n" +
                "  do {\n" +
                "    getline(inputFile, line);\n" +
                "\n" +
                "    // Write your code here.\n" +
                "\n" +
                "  } while(inputFile.good());\n" +
                "\n" +
                "  return 0;\n" +
                "}"
        }

        if (this.showFileUplaodCode === 'True' && (this.codeEditor.getValue() === '' || Object.values(defaulCodes).includes(this.codeEditor.getValue()))) {
            this.codeEditor.setValue(defaulCodes[language]);
        }
    },

    /**
    Update the code editor mode based on the passed language
    **/
    updateEditorMode: function (language) {
        if (language == "Python") {
            this.codeEditor.setOption("mode", { name: "python", version: 3 });
        }
        else if (language == "Java") {
            this.codeEditor.setOption("mode", "text/x-java");
        }
        else if (language == "C++") {
            this.codeEditor.setOption("mode", "text/x-c++src");
        }
        else if (language == "NodeJS") {
            this.codeEditor.setOption("mode", "javascript");
        }
    },

    /**
     Enable/disable the submission and save buttons based on whether
     the user has entered a response.
     **/
    handleResponseChanged: function () {
        this.codeEditor.refresh();
        this.checkSubmissionAbility();

        // Update the save button, save status, and "unsaved changes" warning
        // only if the response has changed

        var saveAbility = this.checkSaveAbility();
        this.saveEnabled(saveAbility);
        this.previewEnabled(saveAbility);
        this.baseView.unsavedWarningEnabled(
            true,
            this.UNSAVED_WARNING_KEY,
            // eslint-disable-next-line max-len
            gettext('If you leave this page without saving or submitting your response, you will lose any work you have done on the response.')
        );


        // Record the current time (used for autosave)
        this.lastChangeTime = Date.now();
    },

    /**
     Save a response without executing and submitting it.
     **/
    autoSaveToServer: function () {
        // If there were errors on previous calls to save, forget
        // about them for now.  If an error occurs on *this* save,
        // we'll set this back to true in the error handler.
        this.errorOnLastSave = false;

        // If no language from dropdown has been selected, show the error and stop the execution
        if (this.getLanguage() === null) {
            this.showLanguageError(gettext("Please select a language from the list"));
            return;
        }

        // Update the save status and error notifications
        this.saveStatus(gettext('Auto save in progress'));

        // Disable the "unsaved changes" warning
        this.baseView.unsavedWarningEnabled(false, this.UNSAVED_WARNING_KEY);

        var view = this;
        var savedResponse = this.response('save');
        view.saveEnabled(false);
        this.server.autoSave(savedResponse).done(function () {
            // Remember which response we saved, once the server confirms that it's been saved...
            view.savedResponse = savedResponse;
            // Update the UI to show Auto Save is complete
            view.saveStatus(gettext('This response has been auto saved but not submitted.'))
            view.saveEnabled(true);
        }).fail(function () {
            view.saveEnabled(true);
            view.saveStatus(gettext('Auto save failed'));
        });
    },

    /**
     Save a response without submitting it.
     **/
    save: function () {
        // If there were errors on previous calls to save, forget
        // about them for now.  If an error occurs on *this* save,
        // we'll set this back to true in the error handler.
        this.errorOnLastSave = false;

        // If no language from dropdown has been selected, show the error and stop the execution
        if (this.getLanguage() === null) {
            this.showLanguageError(gettext("Please select a language from the list"));
            return;
        }

        // Update the save status and error notifications
        this.saveStatus(gettext('Code execution in progress'));
        this.baseView.toggleActionError('save', null);

        // Disable the "unsaved changes" warning
        this.baseView.unsavedWarningEnabled(false, this.UNSAVED_WARNING_KEY);

        var view = this;
        var savedResponse = this.response('save');
        view.saveEnabled(false);
        this.server.save(savedResponse).done(function (data) {
            // Remember which response we saved, once the server confirms that it's been saved...
            view.savedResponse = savedResponse;
            var error = data?.public?.error ?? data?.private?.error
            if (error) {
                if (data?.public?.is_design_problem) {
                    view.showExecutionError(error);
                }
                else {
                    view.showRunError(error);
                }
                view.indicateError();
                view.clearResultSummary();
            }
            else if (!data?.public?.is_design_problem) {
                view.showResultSummary(
                    {
                        correct: data.public.correct,
                        total: data.public.total_tests
                    },
                    data.private ? {
                        correct: data.private.correct,
                        total: data.private.total_tests
                    } : null
                );
                view.showTestCaseResult(data.public.output);
                view.indicateCorrectness(data.public.correct === data.public.total_tests);
            } else {
                view.indicateExecutionSuccess();
                view.showExecutionResults(data.public.output);
            }

            // ... but update the UI based on what the user may have entered
            // since hitting the save button.
            view.checkSubmissionAbility();

            view.saveEnabled(true);
            view.setAutoSaveEnabled(false);
            view.baseView.toggleActionError('save', null);
        }).fail(function (errMsg) {
            view.saveStatus(gettext('Error'));
            view.baseView.toggleActionError('save', errMsg);

            // Remember that an error occurred
            // so we can disable autosave
            // (avoids repeatedly refreshing the error message)
            view.errorOnLastSave = true;
            view.saveEnabled(false);
        });
    },

    /**
     Send a response submission to the server and update the view.
     **/
    submit: function () {

        // If no language is selected, don't do the submission
        if (this.getLanguage() === null) {
            this.showLanguageError(gettext("Please select a language from the list"));
            return;
        }

        // Immediately disable the submit button to prevent multiple submission
        this.submitEnabled(false);

        var view = this;
        var baseView = this.baseView;
        // eslint-disable-next-line new-cap
        var fileDefer = $.Deferred();

        if (view.hasPendingUploadFiles()) {
            if (!view.hasAllUploadFiles()) {
                return;
            } else {
                var msg = gettext('Do you want to upload your file before submitting?');
                if (confirm(msg)) {
                    fileDefer = view.uploadFiles();
                    if (fileDefer === false) {
                        return;
                    }
                }
            }
        } else {
            fileDefer.resolve();
        }

        fileDefer
            .pipe(function () {
                return view.confirmSubmission()
                    // On confirmation, send the submission to the server
                    // The callback returns a promise so we can attach
                    // additional callbacks after the confirmation.
                    // NOTE: in JQuery >=1.8, `pipe()` is deprecated in favor of `then()`,
                    // but we're using JQuery 1.7 in the LMS, so for now we're stuck with `pipe()`.
                    .pipe(function () {
                        var submission = view.response('submit');
                        baseView.toggleActionError('response', null);

                        // Send the submission to the server, returning the promise.
                        view.saveStatus("Creating submission. Please Wait!")
                        return view.server.submit(submission);
                    });
            })

            // If the submission was submitted successfully, move to the next step
            .done($.proxy(view.moveToNextStep, view))

            // Handle submission failure (either a server error or cancellation),
            .fail(function (errCode, errMsg) {
                // If the error is "multiple submissions", then we should move to the next
                // step.  Otherwise, the user will be stuck on the current step with no
                // way to continue.
                if (errCode === 'ENOMULTI') { view.moveToNextStep(); } else {
                    // If there is an error message, display it
                    if (errMsg) { baseView.toggleActionError('submit', errMsg); }

                    // Re-enable the submit button so the user can retry
                    view.submitEnabled(true);
                }
            });
    },

    /**
     Transition the user to the next step in the workflow.
     **/
    moveToNextStep: function () {
        var baseView = this.baseView;
        var usageID = baseView.getUsageID();
        var view = this;

        this.load(usageID);
        baseView.loadAssessmentModules(usageID);

        view.announceStatus = true;

        // Disable the "unsaved changes" warning if the user
        // tries to navigate to another page.
        baseView.unsavedWarningEnabled(false, this.UNSAVED_WARNING_KEY);
    },

    /**
     Make the user confirm before submitting a response.

     Returns:
     JQuery deferred object, which is:
     * resolved if the user confirms the submission
     * rejected if the user cancels the submission
     **/
    confirmSubmission: function () {
        // Keep this on one big line to avoid gettext bug: http://stackoverflow.com/a/24579117
        // eslint-disable-next-line max-len
        var msg = gettext('Are you sure you want to submit your response? After submitting the response, you cannot change or submit a new answer for this problem.');
        // TODO -- UI for confirmation dialog instead of JS confirm
        // eslint-disable-next-line new-cap
        return $.Deferred(function (defer) {
            if (confirm(msg)) { defer.resolve(); } else { defer.reject(); }
        });
    },

    /**
     When selecting a file for upload, do some quick client-side validation
     to ensure that it is an image, a PDF or other allowed types, and is not
     larger than the maximum file size.

     Args:
     files (list): A collection of files used for upload. This function assumes
     there is only one file being uploaded at any time. This file must
     be less than 5 MB and an image, PDF or other allowed types.
     uploadType (string): uploaded file type allowed, could be none, image,
     file or custom.

     **/
    prepareUpload: function (files, uploadType, descriptions) {
        this.files = null;
        this.filesType = uploadType;
        this.filesUploaded = false;

        var totalSize = 0;
        var ext = null;
        var fileType = null;
        var errorCheckerTriggered = false;

        for (var i = 0; i < files.length; i++) {
            totalSize += files[i].size;
            ext = files[i].name.split('.').pop().toLowerCase();
            fileType = files[i].type;

            if (totalSize > this.MAX_FILES_SIZE) {
                this.baseView.toggleActionError(
                    'upload',
                    gettext('File size must be {max_files_mb}MB or less.').replace(
                        '{max_files_mb}',
                        this.MAX_FILES_MB
                    )
                );
                errorCheckerTriggered = true;
                break;
            } else if (uploadType === 'image' && this.data.ALLOWED_IMAGE_MIME_TYPES.indexOf(fileType) === -1) {
                this.baseView.toggleActionError(
                    'upload',
                    gettext('You can upload files with these file types: ') + 'JPG, PNG or GIF'
                );
                errorCheckerTriggered = true;
                break;
            } else if (uploadType === 'pdf-and-image' && this.data.ALLOWED_FILE_MIME_TYPES.indexOf(fileType) === -1) {
                this.baseView.toggleActionError(
                    'upload',
                    gettext('You can upload files with these file types: ') + 'JPG, PNG, GIF or PDF'
                );
                errorCheckerTriggered = true;
                break;
            } else if (uploadType === 'custom' && this.data.FILE_TYPE_WHITE_LIST.indexOf(ext) === -1) {
                this.baseView.toggleActionError(
                    'upload',
                    gettext('You can upload files with these file types: ') +
                    this.data.FILE_TYPE_WHITE_LIST.join(', ')
                );
                errorCheckerTriggered = true;
                break;
            } else if (this.data.FILE_EXT_BLACK_LIST.indexOf(ext) !== -1) {
                this.baseView.toggleActionError(
                    'upload',
                    gettext('File type is not allowed.')
                );
                errorCheckerTriggered = true;
                break;
            }
        }

        if (!errorCheckerTriggered) {
            this.baseView.toggleActionError('upload', null);
            if (files.length > 0) {
                this.files = files;
            }
            this.updateFilesDescriptionsFields(files, descriptions, uploadType);
        }

        if (this.files === null) {
            $(this.element).find('.file__upload').prop('disabled', true);
        }
    },

    /**
     Render textarea fields to input description for each uploaded file.

     */
    /* jshint -W083 */
    updateFilesDescriptionsFields: function (files, descriptions, uploadType) {
        var filesDescriptions = $(this.element).find('.files__descriptions').first();
        var mainDiv = null;
        var divLabel = null;
        var divTextarea = null;
        var divImage = null;
        var img = null;
        var textarea = null;
        var descriptionsExists = true;

        this.filesDescriptions = descriptions || [];

        $(filesDescriptions).show().html('');

        for (var i = 0; i < files.length; i++) {
            mainDiv = $('<div/>');

            divLabel = $('<div/>');
            divLabel.addClass('submission__file__description__label');
            divLabel.text(gettext('Describe ') + files[i].name + ' ' + gettext('(required):'));
            divLabel.appendTo(mainDiv);

            divTextarea = $('<div/>');
            divTextarea.addClass('submission__file__description');
            textarea = $('<textarea />', {
                'aria-label': gettext('Describe ') + files[i].name,
            });
            if ((this.filesDescriptions.indexOf(i) !== -1) && (this.filesDescriptions[i] !== '')) {
                textarea.val(this.filesDescriptions[i]);
            } else {
                descriptionsExists = false;
            }
            textarea.addClass('file__description file__description__' + i);
            textarea.appendTo(divTextarea);

            if (uploadType === 'image') {
                img = $('<img/>', {
                    src: window.URL.createObjectURL(files[i]),
                    height: 80,
                    alt: gettext('Thumbnail view of ') + files[i].name,
                });
                img.onload = function () {
                    window.URL.revokeObjectURL(this.src);
                };

                divImage = $('<div/>');
                divImage.addClass('submission__img__preview');
                img.appendTo(divImage);
                divImage.appendTo(mainDiv);
            }

            divTextarea.appendTo(mainDiv);

            mainDiv.appendTo(filesDescriptions);
            textarea.on('change keyup drop paste', $.proxy(this, 'checkFilesDescriptions'));
        }

        $(this.element).find('.file__upload').prop('disabled', !descriptionsExists);
    },

    /**
     When user type something in some file description field this function check input
     and block/unblock "Upload" button

     */
    checkFilesDescriptions: function () {
        var isError = false;
        var filesDescriptions = [];

        $(this.element).find('.file__description').each(function () {
            var filesDescriptionVal = $.trim($(this).val());
            if (filesDescriptionVal) {
                filesDescriptions.push(filesDescriptionVal);
            } else {
                isError = true;
            }
        });

        $(this.element).find('.file__upload').prop('disabled', isError);
        if (!isError) {
            this.filesDescriptions = filesDescriptions;
        }
    },

    /**
     Clear field with files descriptions.

     */
    removeFilesDescriptions: function () {
        var filesDescriptions = $(this.element).find('.files__descriptions').first();
        $(filesDescriptions).hide().html('');
    },

    /**
     Remove previously uploaded files.

     */
    removeUploadedFiles: function () {
        var view = this;
        var sel = $('.step--response', this.element);

        return this.server.removeUploadedFiles().done(
            function () {
                var sel = $('.step--response', view.element);
                sel.find('.submission__answer__files').html('');
            }
        ).fail(function (errMsg) {
            view.baseView.toggleActionError('upload', errMsg);
            sel.find('.file__upload').prop('disabled', false);
        });
    },

    /**
     Sends request to server to save all file descriptions.

     */
    saveFilesDescriptions: function () {
        var view = this;
        var sel = $('.step--response', this.element);

        return this.server.saveFilesDescriptions(this.filesDescriptions).done(
            function () {
                view.removeFilesDescriptions();
            }
        ).fail(function (errMsg) {
            view.baseView.toggleActionError('upload', errMsg);
            sel.find('.file__upload').prop('disabled', false);
        });
    },

    /**
     Manages file uploads for submission attachments.

     **/
    uploadFiles: function () {
        var view = this;
        var promise = null;
        var fileCount = view.files.length;
        var sel = $('.step--response', this.element);

        sel.find('.file__upload').prop('disabled', true);

        promise = view.removeUploadedFiles();
        promise = promise.then(function () {
            return view.saveFilesDescriptions();
        });

        $.each(view.files, function (index, file) {
            promise = promise.then(function () {
                return view.fileUpload(view, file.type, file.name, index, file, fileCount === (index + 1));
            });
        });

        return promise;
    },

    /**
     Retrieves a one-time upload URL from the server, and uses it to upload images
     to a designated location.

     **/
    fileUpload: function (view, filetype, filename, filenum, file, finalUpload) {
        var sel = $('.step--response', this.element);
        var handleError = function (errMsg) {
            view.baseView.toggleActionError('upload', errMsg);
            sel.find('.file__upload').prop('disabled', false);
        };

        // Call getUploadUrl to get the one-time upload URL for this file. Once
        // completed, execute a sequential AJAX call to upload to the returned
        // URL. This request requires appropriate CORS configuration for AJAX
        // PUT requests on the server.
        return view.server.getUploadUrl(filetype, filename, filenum).done(
            function (url) {
                view.fileUploader.upload(url, file)
                    .done(function () {
                        view.fileUrl(filenum);
                        view.baseView.toggleActionError('upload', null);
                        if (finalUpload) {
                            sel.find('input[type=file]').val('');
                            view.filesUploaded = true;
                            view.checkSubmissionAbility(true);
                        }
                    })
                    .fail(handleError);
            }
        ).fail(handleError);
    },

    /**
     Set the file URL, or retrieve it.

     **/
    fileUrl: function (filenum) {
        var view = this;
        var sel = $('.step--response', this.element);
        view.server.getDownloadUrl(filenum).done(function (url) {
            var className = 'submission__answer__file__block__' + filenum;
            var file = null;
            var img = null;
            var fileBlock = null;
            var fileBlockExists = sel.find('.' + className).length ? true : false;
            var div1 = null;
            var div2 = null;
            var ariaLabelledBy = null;

            if (!fileBlockExists) {
                fileBlock = $('<div/>');
                fileBlock.addClass('submission__answer__file__block ' + className);
                fileBlock.appendTo(sel.find('.submission__answer__files').first());
            }

            if (view.filesType === 'image') {
                ariaLabelledBy = 'file_description_' + Math.random().toString(36).substr(2, 9);

                div1 = $('<div/>', {
                    id: ariaLabelledBy,
                });
                div1.addClass('submission__file__description__label');
                div1.text(view.filesDescriptions[filenum] + ':');
                div1.appendTo(fileBlock);

                img = $('<img />');
                img.addClass('submission__answer__file submission--image');
                img.attr('aria-labelledby', ariaLabelledBy);
                img.attr('src', url);

                div2 = $('<div/>');
                div2.html(img);
                div2.appendTo(fileBlock);
            } else {
                file = $('<a />', {
                    href: url,
                    text: view.filesDescriptions[filenum],
                });
                file.addClass('submission__answer__file submission--file');
                file.attr('target', '_blank');
                file.appendTo(fileBlock);
            }

            return url;
        });
    },
};
