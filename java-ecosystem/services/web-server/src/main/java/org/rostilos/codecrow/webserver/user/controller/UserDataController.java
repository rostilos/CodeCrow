package org.rostilos.codecrow.webserver.user.controller;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.user.UserDTO;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.webserver.user.dto.response.UpdatedUserDataResponse;
import org.rostilos.codecrow.webserver.user.dto.request.UpdateUserDataRequest;
import org.rostilos.codecrow.webserver.user.dto.request.ChangePasswordRequest;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.user.service.UserService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.bind.annotation.*;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/user_info")
public class UserDataController {
    private final UserService userService;
    private final JwtUtils jwtUtils;

    public UserDataController(
            UserService userService,
            JwtUtils jwtUtils
    ) {
        this.userService = userService;
        this.jwtUtils = jwtUtils;
    }

    @GetMapping("/current")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<UserDTO> getCurrentUserInfo(@AuthenticationPrincipal UserDetailsImpl userDetails) {
        UserDTO user = userService.getUserDTOById(userDetails.getId());
        return ResponseEntity.ok(user);
    }

    @PutMapping("/update")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<UpdatedUserDataResponse> updateUserInformation(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody UpdateUserDataRequest updateRequest
    ) {
        userService.updateUserInformation(userDetails.getId(), updateRequest);

        User user = userService.getUserById(userDetails.getId());

        UserDetailsImpl updatedUserDetails = UserDetailsImpl.build(user);
        Authentication authentication = new UsernamePasswordAuthenticationToken(
                updatedUserDetails, null, updatedUserDetails.getAuthorities());
        SecurityContextHolder.getContext().setAuthentication(authentication);
        String jwt = jwtUtils.generateJwtToken(authentication);

        return ResponseEntity.ok(new UpdatedUserDataResponse(
                jwt,
                user.getUsername(),
                user.getEmail(),
                user.getCompany()
        ));
    }

    @PatchMapping("/update-partial")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> partialUpdateUserInformation(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @RequestBody UpdateUserDataRequest updateRequest
    ) {
        userService.partialUpdateUserInformation(userDetails.getId(), updateRequest);
        return ResponseEntity.ok(new MessageResponse("User info successfully updated!"));
    }

    @GetMapping("/check-exists/{userId}")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> checkUserExists(@PathVariable Long userId) {
        boolean exists = userService.userExistsById(userId);
        return ResponseEntity.ok(new MessageResponse("User exists: " + exists));
    }

    @PutMapping("/change-password")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<MessageResponse> changePassword(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody ChangePasswordRequest request
    ) {
         if (!request.getNewPassword().equals(request.getConfirmPassword())) {
             throw new BadCredentialsException("New password and confirmation do not match");
         }

         userService.changePassword(
                 userDetails.getId(),
                 request.getCurrentPassword(),
                 request.getNewPassword()
         );

         return ResponseEntity.ok(new MessageResponse("Password successfully changed"));
    }
}